"""Tests for environment-variable loading (api/_lib/config.py)."""

from __future__ import annotations

import pytest

from _lib.config import load_settings

REQUIRED_ENV = {
    "SUPABASE_URL": "https://example.supabase.co",
    "SUPABASE_SERVICE_ROLE_KEY": "service-role-key",
    "BATCH_SHARED_SECRET": "shared-secret",
}


def _set_env(monkeypatch: pytest.MonkeyPatch, **overrides: str | None) -> None:
    for key, value in REQUIRED_ENV.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("HEALTHCHECKS_TRAINING_PING_URL", raising=False)
    monkeypatch.delenv("ONEMAP_EMAIL", raising=False)
    monkeypatch.delenv("ONEMAP_PASSWORD", raising=False)
    for override_key, override_value in overrides.items():
        if override_value is None:
            monkeypatch.delenv(override_key, raising=False)
        else:
            monkeypatch.setenv(override_key, override_value)


class TestLoadSettings:
    """Tests for load_settings()."""

    def test_loads_all_required_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)

        settings = load_settings()

        assert settings.supabase_url == "https://example.supabase.co"
        assert settings.supabase_service_role_key == "service-role-key"
        assert settings.batch_shared_secret == "shared-secret"
        assert settings.healthchecks_training_ping_url is None
        assert settings.onemap_email is None
        assert settings.onemap_password is None

    def test_onemap_credentials_optional_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unlike SUPABASE_URL/etc, missing OneMap credentials must not raise -- only
        geocode_postal.py needs them, and /api/forecast /api/batch_predict must keep
        working without a OneMap account configured at all."""
        _set_env(monkeypatch)
        monkeypatch.setenv("ONEMAP_EMAIL", "a@b.com")
        monkeypatch.setenv("ONEMAP_PASSWORD", "secret")

        settings = load_settings()

        assert settings.onemap_email == "a@b.com"
        assert settings.onemap_password == "secret"

    def test_strips_trailing_slash_from_supabase_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, SUPABASE_URL="https://example.supabase.co/")

        settings = load_settings()

        assert settings.supabase_url == "https://example.supabase.co"

    def test_healthchecks_url_optional_when_set(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch)
        monkeypatch.setenv("HEALTHCHECKS_TRAINING_PING_URL", "https://hc-ping.com/abc")

        settings = load_settings()

        assert settings.healthchecks_training_ping_url == "https://hc-ping.com/abc"

    @pytest.mark.parametrize(
        "missing_var",
        ["SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY", "BATCH_SHARED_SECRET"],
    )
    def test_missing_required_var_raises(
        self, monkeypatch: pytest.MonkeyPatch, missing_var: str
    ) -> None:
        _set_env(monkeypatch, **{missing_var: None})

        with pytest.raises(RuntimeError, match=missing_var):
            load_settings()

    def test_blank_required_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _set_env(monkeypatch, BATCH_SHARED_SECRET="   ")

        with pytest.raises(RuntimeError, match="BATCH_SHARED_SECRET"):
            load_settings()
