"""Tests for Gemini Flash integration client."""

from pathlib import Path
from unittest.mock import patch


def test_gemini_graceful_when_unconfigured(tmp_path):
    cfg = tmp_path / "config.json"
    cfg.write_text("{}")

    with patch("enki.integrations.gemini.ENKI_ROOT", Path(tmp_path)):
        from enki.integrations.gemini import is_configured, call_gemini

        assert is_configured() is False
        result = call_gemini("sys", "user")
        assert result["content"] is None
        assert "not configured" in (result["error"] or "").lower()
