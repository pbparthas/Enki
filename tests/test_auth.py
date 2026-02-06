"""Tests for auth module — JWT create/verify/refresh, token storage."""

import time
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import jwt
import pytest

from enki.auth import (
    ACCESS_TOKEN_EXPIRE_MINUTES,
    ALGORITHM,
    REFRESH_TOKEN_EXPIRE_DAYS,
    TokenPair,
    clear_stored_tokens,
    create_token_pair,
    get_access_token,
    get_stored_tokens,
    is_refresh_token_valid,
    needs_refresh,
    refresh_access_token,
    store_tokens,
    verify_access_token,
    verify_refresh_token,
)
from enki.db import close_db, init_db, set_db_path

SECRET = "test-secret-key-for-jwt"
USER_ID = "test-user"


@pytest.fixture
def temp_db(tmp_path):
    """Create a temporary database for auth tests."""
    db_path = tmp_path / ".enki" / "wisdom.db"
    db_path.parent.mkdir(parents=True, exist_ok=True)
    init_db(db_path)
    yield tmp_path
    close_db()
    set_db_path(None)


# --- Token Creation ---


class TestCreateTokenPair:
    def test_returns_token_pair(self):
        pair = create_token_pair(USER_ID, SECRET)
        assert isinstance(pair, TokenPair)
        assert pair.access_token
        assert pair.refresh_token
        assert pair.access_token != pair.refresh_token

    def test_access_token_has_correct_claims(self):
        pair = create_token_pair(USER_ID, SECRET)
        payload = jwt.decode(pair.access_token, SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == USER_ID
        assert payload["type"] == "access"
        assert "exp" in payload
        assert "iat" in payload

    def test_refresh_token_has_correct_claims(self):
        pair = create_token_pair(USER_ID, SECRET)
        payload = jwt.decode(pair.refresh_token, SECRET, algorithms=[ALGORITHM])
        assert payload["sub"] == USER_ID
        assert payload["type"] == "refresh"
        assert "exp" in payload
        assert "iat" in payload

    def test_access_expires_within_expected_window(self):
        before = datetime.now(timezone.utc)
        pair = create_token_pair(USER_ID, SECRET)
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        expected_max = after + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
        assert expected_min <= pair.access_expires <= expected_max

    def test_refresh_expires_within_expected_window(self):
        before = datetime.now(timezone.utc)
        pair = create_token_pair(USER_ID, SECRET)
        after = datetime.now(timezone.utc)

        expected_min = before + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        expected_max = after + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
        assert expected_min <= pair.refresh_expires <= expected_max


# --- Token Verification ---


class TestVerifyAccessToken:
    def test_valid_token(self):
        pair = create_token_pair(USER_ID, SECRET)
        payload = verify_access_token(pair.access_token, SECRET)
        assert payload is not None
        assert payload["sub"] == USER_ID
        assert payload["type"] == "access"

    def test_wrong_secret_returns_none(self):
        pair = create_token_pair(USER_ID, SECRET)
        assert verify_access_token(pair.access_token, "wrong-secret") is None

    def test_refresh_token_rejected(self):
        """Access verifier rejects refresh tokens."""
        pair = create_token_pair(USER_ID, SECRET)
        assert verify_access_token(pair.refresh_token, SECRET) is None

    def test_expired_token_returns_none(self):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": USER_ID,
            "type": "access",
            "exp": now - timedelta(seconds=10),
            "iat": now - timedelta(hours=1),
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        assert verify_access_token(token, SECRET) is None

    def test_malformed_token_returns_none(self):
        assert verify_access_token("not.a.valid.token", SECRET) is None

    def test_empty_token_returns_none(self):
        assert verify_access_token("", SECRET) is None


class TestVerifyRefreshToken:
    def test_valid_token(self):
        pair = create_token_pair(USER_ID, SECRET)
        payload = verify_refresh_token(pair.refresh_token, SECRET)
        assert payload is not None
        assert payload["sub"] == USER_ID
        assert payload["type"] == "refresh"

    def test_wrong_secret_returns_none(self):
        pair = create_token_pair(USER_ID, SECRET)
        assert verify_refresh_token(pair.refresh_token, "wrong-secret") is None

    def test_access_token_rejected(self):
        """Refresh verifier rejects access tokens."""
        pair = create_token_pair(USER_ID, SECRET)
        assert verify_refresh_token(pair.access_token, SECRET) is None

    def test_expired_token_returns_none(self):
        now = datetime.now(timezone.utc)
        payload = {
            "sub": USER_ID,
            "type": "refresh",
            "exp": now - timedelta(seconds=10),
            "iat": now - timedelta(days=31),
        }
        token = jwt.encode(payload, SECRET, algorithm=ALGORITHM)
        assert verify_refresh_token(token, SECRET) is None


# --- Token Storage (SQLite fallback path) ---


class TestTokenStorage:
    def test_store_and_retrieve(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)

        retrieved = get_stored_tokens()
        assert retrieved is not None
        assert retrieved.access_token == pair.access_token
        assert retrieved.refresh_token == pair.refresh_token

    def test_store_overwrites_previous(self, temp_db):
        pair1 = create_token_pair("user-1", SECRET)
        store_tokens(pair1)

        pair2 = create_token_pair("user-2", SECRET)
        store_tokens(pair2)

        retrieved = get_stored_tokens()
        assert retrieved.access_token == pair2.access_token

    def test_get_returns_none_when_empty(self, temp_db):
        assert get_stored_tokens() is None

    def test_clear_tokens(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)
        assert get_stored_tokens() is not None

        clear_stored_tokens()
        assert get_stored_tokens() is None

    def test_clear_when_already_empty(self, temp_db):
        """Clearing when no tokens stored should not raise."""
        clear_stored_tokens()

    def test_sqlite_fallback_when_keyring_unavailable(self, temp_db):
        """Tokens stored via SQLite when keyring is not available."""
        with patch("enki.auth._keyring_available", False):
            pair = create_token_pair(USER_ID, SECRET)
            store_tokens(pair)

        with patch("enki.auth._keyring_available", False):
            retrieved = get_stored_tokens()
            assert retrieved is not None
            assert retrieved.access_token == pair.access_token


# --- Refresh Logic ---


class TestNeedsRefresh:
    def test_returns_true_when_no_tokens(self, temp_db):
        assert needs_refresh() is True

    def test_returns_false_for_fresh_token(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)
        assert needs_refresh() is False

    def test_returns_true_within_buffer(self, temp_db):
        """Token expiring within buffer window should trigger refresh."""
        now = datetime.now(timezone.utc)
        pair = TokenPair(
            access_token=jwt.encode(
                {"sub": USER_ID, "type": "access",
                 "exp": now + timedelta(minutes=3), "iat": now},
                SECRET, algorithm=ALGORITHM,
            ),
            refresh_token=jwt.encode(
                {"sub": USER_ID, "type": "refresh",
                 "exp": now + timedelta(days=30), "iat": now},
                SECRET, algorithm=ALGORITHM,
            ),
            access_expires=now + timedelta(minutes=3),
            refresh_expires=now + timedelta(days=30),
        )
        store_tokens(pair)
        # Default buffer is 5 min; token expires in 3 min → needs refresh
        assert needs_refresh(buffer_minutes=5) is True

    def test_returns_false_outside_buffer(self, temp_db):
        now = datetime.now(timezone.utc)
        pair = TokenPair(
            access_token=jwt.encode(
                {"sub": USER_ID, "type": "access",
                 "exp": now + timedelta(minutes=30), "iat": now},
                SECRET, algorithm=ALGORITHM,
            ),
            refresh_token=jwt.encode(
                {"sub": USER_ID, "type": "refresh",
                 "exp": now + timedelta(days=30), "iat": now},
                SECRET, algorithm=ALGORITHM,
            ),
            access_expires=now + timedelta(minutes=30),
            refresh_expires=now + timedelta(days=30),
        )
        store_tokens(pair)
        assert needs_refresh(buffer_minutes=5) is False


class TestIsRefreshTokenValid:
    def test_returns_false_when_no_tokens(self, temp_db):
        assert is_refresh_token_valid() is False

    def test_returns_true_for_valid_refresh(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)
        assert is_refresh_token_valid() is True

    def test_returns_false_for_expired_refresh(self, temp_db):
        now = datetime.now(timezone.utc)
        pair = TokenPair(
            access_token="expired-access",
            refresh_token="expired-refresh",
            access_expires=now - timedelta(hours=1),
            refresh_expires=now - timedelta(seconds=1),
        )
        store_tokens(pair)
        assert is_refresh_token_valid() is False


class TestRefreshAccessToken:
    def test_refreshes_successfully(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        # Manually set access_expires to past so needs_refresh() returns True
        now = datetime.now(timezone.utc)
        pair_near_expiry = TokenPair(
            access_token=jwt.encode(
                {"sub": USER_ID, "type": "access",
                 "exp": now + timedelta(seconds=30), "iat": now},
                SECRET, algorithm=ALGORITHM,
            ),
            refresh_token=pair.refresh_token,
            access_expires=now + timedelta(seconds=30),
            refresh_expires=pair.refresh_expires,
        )
        store_tokens(pair_near_expiry)

        new_pair = refresh_access_token(SECRET)
        assert new_pair is not None
        assert new_pair.access_token != pair_near_expiry.access_token

    def test_returns_none_when_no_tokens(self, temp_db):
        assert refresh_access_token(SECRET) is None

    def test_returns_none_with_invalid_refresh(self, temp_db):
        now = datetime.now(timezone.utc)
        pair = TokenPair(
            access_token="any",
            refresh_token="invalid-jwt",
            access_expires=now - timedelta(hours=1),
            refresh_expires=now + timedelta(days=30),
        )
        store_tokens(pair)
        assert refresh_access_token(SECRET) is None

    def test_returns_existing_if_no_refresh_needed(self, temp_db):
        """Double-check pattern: if another thread already refreshed, return current."""
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)
        # Token is fresh — refresh should return current tokens
        result = refresh_access_token(SECRET)
        assert result is not None
        assert result.access_token == pair.access_token


class TestGetAccessToken:
    def test_returns_none_when_no_tokens(self, temp_db):
        assert get_access_token() is None

    def test_returns_token_when_valid(self, temp_db):
        pair = create_token_pair(USER_ID, SECRET)
        store_tokens(pair)
        token = get_access_token()
        assert token == pair.access_token

    def test_returns_none_when_expired(self, temp_db):
        now = datetime.now(timezone.utc)
        pair = TokenPair(
            access_token=jwt.encode(
                {"sub": USER_ID, "type": "access",
                 "exp": now - timedelta(seconds=10), "iat": now - timedelta(hours=1)},
                SECRET, algorithm=ALGORITHM,
            ),
            refresh_token="any",
            access_expires=now - timedelta(seconds=10),
            refresh_expires=now + timedelta(days=30),
        )
        store_tokens(pair)
        assert get_access_token() is None
