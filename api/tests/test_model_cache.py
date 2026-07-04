"""Tests for the warm-instance model cache (api/_lib/model_cache.py)."""

from __future__ import annotations

import numpy as np
import pytest

from _lib.model_cache import ModelCache, ModelLoadError, get_shared_cache


class _FakeBoosterFetchCounter:
    """Records how many times a fetch callable was invoked."""

    def __init__(self, model_str: str) -> None:
        self.model_str = model_str
        self.call_count = 0

    def __call__(self) -> str:
        self.call_count += 1
        return self.model_str


class TestModelCacheGet:
    """Tests for ModelCache.get() caching/reload-on-change semantics."""

    def test_cache_miss_loads_and_caches(self, tiny_lightgbm_model_str: str) -> None:
        cache = ModelCache()
        fetch = _FakeBoosterFetchCounter(tiny_lightgbm_model_str)

        booster = cache.get("v1", fetch)

        assert fetch.call_count == 1
        assert booster is not None
        assert cache.loaded_versions() == frozenset({"v1"})

    def test_cache_hit_does_not_refetch(self, tiny_lightgbm_model_str: str) -> None:
        cache = ModelCache()
        fetch = _FakeBoosterFetchCounter(tiny_lightgbm_model_str)
        cache.get("v1", fetch)

        second = cache.get("v1", fetch)

        assert fetch.call_count == 1  # not called again
        assert second is cache.get("v1", fetch)  # same object every time

    def test_version_change_triggers_exactly_one_new_fetch(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        fetch_v1 = _FakeBoosterFetchCounter(tiny_lightgbm_model_str)
        fetch_v2 = _FakeBoosterFetchCounter(tiny_lightgbm_model_str)

        cache.get("v1", fetch_v1)
        cache.get("v2", fetch_v2)

        assert fetch_v1.call_count == 1
        assert fetch_v2.call_count == 1
        assert cache.loaded_versions() == frozenset({"v1", "v2"})

    def test_fetch_failure_raises_model_load_error_and_is_not_cached(self) -> None:
        cache = ModelCache()

        def failing_fetch() -> str:
            raise TimeoutError("storage unreachable")

        with pytest.raises(ModelLoadError):
            cache.get("v1", failing_fetch)

        assert cache.loaded_versions() == frozenset()

    def test_corrupt_model_string_raises_model_load_error(self) -> None:
        cache = ModelCache()

        with pytest.raises(ModelLoadError):
            cache.get("v1", lambda: "this is not a valid lightgbm model")

    def test_failed_load_does_not_disturb_existing_last_known_good(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        cache.get("v1", _FakeBoosterFetchCounter(tiny_lightgbm_model_str))

        with pytest.raises(ModelLoadError):
            cache.get("v2", lambda: "corrupt")

        result = cache.last_known_good()
        assert result is not None
        version, booster = result
        assert version == "v1"
        assert booster is not None

    def test_retry_after_transient_failure_succeeds_on_next_call(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        """A version that failed once is not permanently blacklisted."""
        cache = ModelCache()
        attempts = {"n": 0}

        def flaky_fetch() -> str:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise TimeoutError("transient")
            return tiny_lightgbm_model_str

        with pytest.raises(ModelLoadError):
            cache.get("v1", flaky_fetch)

        booster = cache.get("v1", flaky_fetch)  # same version, retried

        assert booster is not None
        assert attempts["n"] == 2


class TestLastKnownGood:
    """Tests for ModelCache.last_known_good()."""

    def test_none_when_nothing_ever_loaded(self) -> None:
        cache = ModelCache()

        assert cache.last_known_good() is None

    def test_returns_most_recently_loaded_version(self, tiny_lightgbm_model_str: str) -> None:
        cache = ModelCache()
        cache.get("v1", _FakeBoosterFetchCounter(tiny_lightgbm_model_str))
        cache.get("v2", _FakeBoosterFetchCounter(tiny_lightgbm_model_str))

        result = cache.last_known_good()
        assert result is not None
        version, _ = result

        assert version == "v2"

    def test_reselecting_older_cached_version_updates_last_known_good(
        self, tiny_lightgbm_model_str: str
    ) -> None:
        cache = ModelCache()
        fetch = _FakeBoosterFetchCounter(tiny_lightgbm_model_str)
        cache.get("v1", fetch)
        cache.get("v2", fetch)

        cache.get("v1", fetch)  # cache hit on the older version

        result = cache.last_known_good()
        assert result is not None
        version, _ = result
        assert version == "v1"


class TestSharedCache:
    """Tests for the module-level get_shared_cache() singleton."""

    def test_returns_the_same_instance_across_calls(self) -> None:
        assert get_shared_cache() is get_shared_cache()


class TestRealBoosterPredicts:
    """Sanity check that the cached real booster can actually predict."""

    def test_predict_on_feature_vector_shape(self, tiny_lightgbm_model_str: str) -> None:
        cache = ModelCache()
        booster = cache.get("v1", lambda: tiny_lightgbm_model_str)

        # [dow, slot_of_day, is_holiday, lots_now, lots_15m_ago, lots_30m_ago, lots_60m_ago]
        features = np.array([[2, 40, 0, 120, 118, 115, 110]], dtype=np.float64)
        # Booster.predict()'s declared return type is a broad union
        # (ndarray | sparse matrix | list); a dense array input always
        # yields a dense ndarray back, and np.asarray() makes that concrete
        # for the type checker too (a no-op at runtime in that case).
        prediction = np.asarray(booster.predict(features))

        assert prediction.shape == (1,)
        assert np.isfinite(prediction[0])
