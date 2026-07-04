"""Warm-instance cache of LightGBM boosters, keyed by model_config version.

Reload only on version change (Premise #9): a Vercel Python function's warm
instance keeps this module's state alive across invocations, so the model
artifact is fetched from Supabase Storage at most once per version rather
than once per batch run.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import lightgbm

logger = logging.getLogger(__name__)


class ModelLoadError(Exception):
    """Raised when a model artifact cannot be fetched from Storage or
    parsed into a LightGBM Booster.
    """


class ModelCache:
    """Maps model version -> loaded LightGBM Booster, with a
    last-known-good pointer for fallback on a bad new version.

    A single module-level instance (see :func:`get_shared_cache`) is reused
    across warm invocations in production; tests construct their own
    isolated instances to avoid cross-test state leakage.
    """

    def __init__(self) -> None:
        self._boosters: dict[str, lightgbm.Booster] = {}
        self._last_good_version: str | None = None

    def get(self, version: str, fetch_model_str: Callable[[], str]) -> lightgbm.Booster:
        """Return the cached booster for ``version``, loading it on a miss.

        Args:
            version: The ``model_config.active_model_version`` value.
            fetch_model_str: Zero-arg callable invoked ONLY on a cache miss;
                must return the raw LightGBM text-model string (e.g. read
                from Supabase Storage). Not called at all on a cache hit --
                this is what makes "reload only on version change" true.

        Returns:
            The loaded (or cached) Booster for ``version``.

        Raises:
            ModelLoadError: If ``fetch_model_str`` raises, or the returned
                text cannot be parsed into a Booster. A failed load is
                deliberately NOT cached, so a later call for the same
                version retries the fetch instead of being permanently
                blacklisted after one transient failure (e.g. a Storage
                blip), while a previously-cached good version is left
                untouched for :meth:`last_known_good` to fall back on.
        """
        if version in self._boosters:
            self._last_good_version = version
            return self._boosters[version]

        logger.info("model_cache: loading new model artifact version=%s", version)
        try:
            model_str = fetch_model_str()
            booster = lightgbm.Booster(model_str=model_str)
        except Exception as exc:
            logger.error("model_cache: failed to load version=%s: %s", version, exc)
            raise ModelLoadError(f"failed to load model artifact version={version!r}") from exc

        self._boosters[version] = booster
        self._last_good_version = version
        return booster

    def last_known_good(self) -> tuple[str, lightgbm.Booster] | None:
        """Return the (version, booster) pair most recently loaded OK.

        Returns:
            None if nothing has ever loaded successfully in this cache's
            lifetime, else the last-known-good ``(version, booster)`` pair.
        """
        if self._last_good_version is None:
            return None
        return self._last_good_version, self._boosters[self._last_good_version]

    def loaded_versions(self) -> frozenset[str]:
        """Return the set of currently cached version keys (test/debug aid)."""
        return frozenset(self._boosters)


# Module-level singleton: reused across warm invocations of the same Vercel
# Python instance. Production code goes through `get_shared_cache()` rather
# than constructing its own ModelCache, or warm-instance reuse would
# silently stop working.
_SHARED_CACHE = ModelCache()


def get_shared_cache() -> ModelCache:
    """Return the process-wide :class:`ModelCache` singleton used in production."""
    return _SHARED_CACHE
