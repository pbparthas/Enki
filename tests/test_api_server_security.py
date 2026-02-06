"""Tests for API server security fixes (P0-09, P0-10).

Skipped if fastapi is not installed (server dependencies not in dev venv).
"""

import os
import pytest
from unittest.mock import patch

fastapi = pytest.importorskip("fastapi", reason="fastapi not installed (server dep)")


class TestCORSOrigins:
    """P0-09: CORS must not use wildcard with credentials."""

    def test_default_origins(self):
        """Default CORS origins should be localhost only, not wildcard."""
        with patch.dict(os.environ, {}, clear=True):
            import importlib
            import enki.api_server as mod
            importlib.reload(mod)

            assert "*" not in mod.CORS_ORIGINS
            assert "http://localhost:3000" in mod.CORS_ORIGINS
            assert "http://localhost:8002" in mod.CORS_ORIGINS

    def test_custom_origins_from_env(self):
        """ENKI_CORS_ORIGINS env var sets allowed origins."""
        with patch.dict(os.environ, {"ENKI_CORS_ORIGINS": "https://app.example.com,https://admin.example.com"}):
            import importlib
            import enki.api_server as mod
            importlib.reload(mod)

            assert "https://app.example.com" in mod.CORS_ORIGINS
            assert "https://admin.example.com" in mod.CORS_ORIGINS
            assert "http://localhost:3000" not in mod.CORS_ORIGINS


class TestAPIKeyValidation:
    """P0-10: Empty API_KEY and JWT_SECRET must refuse to start."""

    @pytest.mark.anyio
    async def test_empty_api_key_refuses_start(self):
        """Server refuses to start with empty API key."""
        with patch.dict(os.environ, {"ENKI_API_KEY": "", "ENKI_JWT_SECRET": "test-secret"}):
            import importlib
            import enki.api_server as mod
            importlib.reload(mod)

            with pytest.raises(RuntimeError, match="ENKI_API_KEY must be set"):
                await mod.startup_event()

    @pytest.mark.anyio
    async def test_empty_jwt_secret_refuses_start(self):
        """Server refuses to start with empty JWT secret."""
        with patch.dict(os.environ, {"ENKI_API_KEY": "test-key", "ENKI_JWT_SECRET": ""}):
            import importlib
            import enki.api_server as mod
            importlib.reload(mod)

            with pytest.raises(RuntimeError, match="ENKI_JWT_SECRET must be set"):
                await mod.startup_event()
