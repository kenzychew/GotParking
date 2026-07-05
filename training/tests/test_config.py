"""Tests for gotparking_training.config: env-var loading and the
cross-service constant contract with api/_lib/config.py.
"""

from __future__ import annotations

import pytest

from gotparking_training.config import (
    COLD_START_MIN_AGE_HOURS,
    COLD_START_MIN_SAMPLES,
    FORECAST_HORIZON_MINUTES,
    MODEL_STORAGE_BUCKET,
    load_settings,
)

from tests._load_api_module import api_lib_on_path, load_api_lib_module


class TestLoadSettings:
    """Tests for load_settings()."""

    def test_loads_all_values_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co/")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
        monkeypatch.setenv("HEALTHCHECKS_TRAINING_PING_URL", "https://hc-ping.com/abc")

        settings = load_settings()

        assert settings.supabase_url == "https://xyz.supabase.co"  # trailing slash stripped
        assert settings.supabase_service_role_key == "service-key"
        assert settings.healthchecks_training_ping_url == "https://hc-ping.com/abc"

    def test_healthchecks_url_optional(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "https://xyz.supabase.co")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")
        monkeypatch.delenv("HEALTHCHECKS_TRAINING_PING_URL", raising=False)

        settings = load_settings()

        assert settings.healthchecks_training_ping_url is None

    def test_missing_required_var_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SUPABASE_URL", raising=False)
        monkeypatch.delenv("SUPABASE_SERVICE_ROLE_KEY", raising=False)

        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            load_settings()

    def test_blank_required_var_treated_as_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SUPABASE_URL", "   ")
        monkeypatch.setenv("SUPABASE_SERVICE_ROLE_KEY", "service-key")

        with pytest.raises(RuntimeError, match="SUPABASE_URL"):
            load_settings()


class TestApiCrossCheck:
    """The cold-start/storage-bucket/horizon constants must match
    api/_lib/config.py exactly -- a mismatch would mean training and
    serving disagree about which carparks are warmed up, or training
    would upload artifacts to a bucket serving never reads from.
    """

    def test_cold_start_and_storage_constants_match_api(self) -> None:
        with api_lib_on_path():
            api_config = load_api_lib_module("config")

        assert COLD_START_MIN_AGE_HOURS == api_config.COLD_START_MIN_AGE_HOURS
        assert COLD_START_MIN_SAMPLES == api_config.COLD_START_MIN_SAMPLES
        assert FORECAST_HORIZON_MINUTES == api_config.FORECAST_HORIZON_MINUTES
        assert MODEL_STORAGE_BUCKET == api_config.MODEL_STORAGE_BUCKET
