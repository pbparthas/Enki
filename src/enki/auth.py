"""JWT authentication module for Enki.

Handles token creation, verification, storage, and refresh.
Uses OS keychain (via keyring) for secure token storage,
with SQLite fallback for headless environments.
"""

import logging
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Optional

import jwt

logger = logging.getLogger(__name__)

from .db import get_db, init_db

# Token configuration
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_DAYS = 30
ALGORITHM = "HS256"

# Lock for thread-safe token refresh
_refresh_lock = threading.Lock()

# Try to import keyring, fall back to None if unavailable
try:
    import keyring
    _keyring_available = True
except ImportError:
    keyring = None
    _keyring_available = False


@dataclass
class TokenPair:
    """JWT access and refresh token pair."""
    access_token: str
    refresh_token: str
    access_expires: datetime
    refresh_expires: datetime


def create_token_pair(user_id: str, secret: str) -> TokenPair:
    """Create a new access + refresh token pair.

    Args:
        user_id: Identifier for the user/machine
        secret: JWT signing secret

    Returns:
        TokenPair with both tokens and expiry times
    """
    now = datetime.now(timezone.utc)
    access_expires = now + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    refresh_expires = now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)

    access_payload = {
        "sub": user_id,
        "type": "access",
        "exp": access_expires,
        "iat": now,
    }

    refresh_payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": refresh_expires,
        "iat": now,
    }

    access_token = jwt.encode(access_payload, secret, algorithm=ALGORITHM)
    refresh_token = jwt.encode(refresh_payload, secret, algorithm=ALGORITHM)

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires=access_expires,
        refresh_expires=refresh_expires,
    )


def verify_access_token(token: str, secret: str) -> Optional[dict]:
    """Verify an access token and return its payload.

    Args:
        token: JWT access token
        secret: JWT signing secret

    Returns:
        Token payload dict if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        if payload.get("type") != "access":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def verify_refresh_token(token: str, secret: str) -> Optional[dict]:
    """Verify a refresh token and return its payload.

    Args:
        token: JWT refresh token
        secret: JWT signing secret

    Returns:
        Token payload dict if valid, None if invalid/expired
    """
    try:
        payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
        if payload.get("type") != "refresh":
            return None
        return payload
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def store_tokens(tokens: TokenPair) -> None:
    """Store tokens securely.

    Uses OS keychain via keyring if available, otherwise SQLite.
    Token strings go in keychain (secure), expiry times in SQLite.
    """
    init_db()
    conn = get_db()

    # Store expiry times in SQLite
    conn.execute("""
        INSERT OR REPLACE INTO auth_tokens (id, access_expires, refresh_expires)
        VALUES (1, ?, ?)
    """, (tokens.access_expires.isoformat(), tokens.refresh_expires.isoformat()))
    conn.commit()

    # Store actual tokens
    if _keyring_available:
        try:
            keyring.set_password("enki", "access_token", tokens.access_token)
            keyring.set_password("enki", "refresh_token", tokens.refresh_token)
            return
        except Exception as e:
            # Fall through to SQLite storage
            logger.warning("Non-fatal error in auth (keyring store): %s", e)
            pass

    # Fallback: store in SQLite (less secure but works on headless servers)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS auth_tokens_fallback (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            access_token TEXT,
            refresh_token TEXT
        )
    """)
    conn.execute("""
        INSERT OR REPLACE INTO auth_tokens_fallback (id, access_token, refresh_token)
        VALUES (1, ?, ?)
    """, (tokens.access_token, tokens.refresh_token))
    conn.commit()


def get_stored_tokens() -> Optional[TokenPair]:
    """Retrieve stored tokens.

    Returns:
        TokenPair if tokens exist and are valid, None otherwise
    """
    init_db()
    conn = get_db()

    # Get expiry times from SQLite
    row = conn.execute("""
        SELECT access_expires, refresh_expires FROM auth_tokens WHERE id = 1
    """).fetchone()

    if not row:
        return None

    access_expires = datetime.fromisoformat(row["access_expires"])
    refresh_expires = datetime.fromisoformat(row["refresh_expires"])

    # Get tokens from keyring or fallback
    access_token = None
    refresh_token = None

    if _keyring_available:
        try:
            access_token = keyring.get_password("enki", "access_token")
            refresh_token = keyring.get_password("enki", "refresh_token")
        except Exception as e:
            logger.warning("Non-fatal error in auth (keyring retrieve): %s", e)
            pass

    # Fallback to SQLite
    if not access_token or not refresh_token:
        fallback = conn.execute("""
            SELECT access_token, refresh_token FROM auth_tokens_fallback WHERE id = 1
        """).fetchone()
        if fallback:
            access_token = fallback["access_token"]
            refresh_token = fallback["refresh_token"]

    if not access_token or not refresh_token:
        return None

    return TokenPair(
        access_token=access_token,
        refresh_token=refresh_token,
        access_expires=access_expires,
        refresh_expires=refresh_expires,
    )


def clear_stored_tokens() -> None:
    """Clear all stored tokens (logout)."""
    init_db()
    conn = get_db()

    # Clear from SQLite
    conn.execute("DELETE FROM auth_tokens WHERE id = 1")
    try:
        conn.execute("DELETE FROM auth_tokens_fallback WHERE id = 1")
    except sqlite3.OperationalError:
        pass  # Table may not exist if keyring was always used
    conn.commit()

    # Clear from keyring
    if _keyring_available:
        try:
            keyring.delete_password("enki", "access_token")
            keyring.delete_password("enki", "refresh_token")
        except Exception as e:
            logger.warning("Non-fatal error in auth (keyring clear): %s", e)
            pass


def needs_refresh(buffer_minutes: int = 5) -> bool:
    """Check if access token needs refresh.

    Args:
        buffer_minutes: Refresh this many minutes before actual expiry

    Returns:
        True if token should be refreshed, False otherwise
    """
    tokens = get_stored_tokens()
    if not tokens:
        return True

    now = datetime.now(timezone.utc)
    # Handle both aware and naive datetimes
    access_expires = tokens.access_expires
    if access_expires.tzinfo is None:
        access_expires = access_expires.replace(tzinfo=timezone.utc)

    buffer = timedelta(minutes=buffer_minutes)
    return now >= (access_expires - buffer)


def is_refresh_token_valid() -> bool:
    """Check if refresh token is still valid (not expired)."""
    tokens = get_stored_tokens()
    if not tokens:
        return False

    now = datetime.now(timezone.utc)
    # Handle both aware and naive datetimes
    refresh_expires = tokens.refresh_expires
    if refresh_expires.tzinfo is None:
        refresh_expires = refresh_expires.replace(tzinfo=timezone.utc)

    return now < refresh_expires


def refresh_access_token(secret: str) -> Optional[TokenPair]:
    """Refresh the access token using the refresh token.

    Thread-safe: uses lock to prevent concurrent refresh.

    Args:
        secret: JWT signing secret

    Returns:
        New TokenPair if successful, None if refresh token invalid
    """
    with _refresh_lock:
        # Double-check after acquiring lock
        if not needs_refresh():
            return get_stored_tokens()

        tokens = get_stored_tokens()
        if not tokens:
            return None

        # Verify refresh token
        payload = verify_refresh_token(tokens.refresh_token, secret)
        if not payload:
            return None

        # Create new token pair
        user_id = payload.get("sub", "enki-client")
        new_tokens = create_token_pair(user_id, secret)
        store_tokens(new_tokens)

        return new_tokens


def get_access_token() -> Optional[str]:
    """Get the current access token if valid.

    Returns:
        Access token string, or None if not available/expired
    """
    tokens = get_stored_tokens()
    if not tokens:
        return None

    # Check if expired
    now = datetime.now(timezone.utc)
    access_expires = tokens.access_expires
    if access_expires.tzinfo is None:
        access_expires = access_expires.replace(tzinfo=timezone.utc)

    if now >= access_expires:
        return None

    return tokens.access_token
