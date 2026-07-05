"""Load modules from the sibling ``api/_lib`` package for cross-checks only.

``api/`` and ``training/`` are separate uv projects (separate pyproject.toml,
separate virtual environments, separately deployed -- Vercel vs. GitHub
Actions), so training's tests cannot simply ``import _lib...`` the way api's
own tests do. api's own pytest config resolves this via
``pythonpath = ["."]`` combined with cwd=api/; this module recreates that
condition on demand, scoped to a single ``with`` block, for training's
cross-check tests only.

``api/_lib/features.py`` does ``from _lib.sg_time import ...``, so loading
it standalone (e.g. via ``importlib.util.spec_from_file_location``) fails
unless ``_lib`` is a resolvable top-level package -- which requires api/'s
directory (not api/_lib itself) to be on ``sys.path``. Hence this helper
inserts api/ onto ``sys.path`` for the duration of the ``with`` block and
evicts any ``_lib``/``_lib.*`` entries from ``sys.modules`` both before and
after, so this never leaks api's modules into the rest of training's test
session (training has no module of its own named ``_lib``, but being
careful here costs nothing and avoids ever depending on that coincidence).

Used exclusively by cross-check tests asserting training's copies of the
SGT/feature contract agree bit-for-bit with the serving side's originals
(the CRITICAL INTEGRATION CONTRACT for T5) -- never used by production code.
"""

from __future__ import annotations

import importlib
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType

#: Repository root, computed relative to this file
#: (training/tests/_load_api_module.py -> parents[2] == repo root).
_REPO_ROOT = Path(__file__).resolve().parents[2]
_API_DIR = _REPO_ROOT / "api"


def _evict_lib_modules() -> dict[str, ModuleType]:
    """Remove and return any currently-loaded `_lib`/`_lib.*` sys.modules entries."""
    stashed = {
        name: module
        for name, module in sys.modules.items()
        if name == "_lib" or name.startswith("_lib.")
    }
    for name in stashed:
        del sys.modules[name]
    return stashed


@contextmanager
def api_lib_on_path() -> Iterator[None]:
    """Temporarily put api/ on sys.path so api's `_lib.*` imports resolve.

    Raises:
        FileNotFoundError: If the api/ directory does not exist -- surfaced
            loudly rather than silently skipping the cross-check, since a
            missing directory here means the integration contract can no
            longer be verified at all.
    """
    if not _API_DIR.is_dir():
        raise FileNotFoundError(
            f"expected api/ directory not found at {_API_DIR} -- cannot verify "
            "the sg_time/features cross-check contract"
        )

    path_str = str(_API_DIR)
    inserted = path_str not in sys.path
    if inserted:
        sys.path.insert(0, path_str)
    stashed = _evict_lib_modules()
    try:
        yield
    finally:
        _evict_lib_modules()
        sys.modules.update(stashed)
        if inserted:
            sys.path.remove(path_str)


def load_api_lib_module(module_name: str) -> ModuleType:
    """Import a module from api/_lib by its short name.

    Must be called within an :func:`api_lib_on_path` context.

    Args:
        module_name: The module's name within api/_lib, e.g. "sg_time" or
            "features" (no "_lib." prefix, no ".py" suffix).

    Returns:
        The imported module object.
    """
    return importlib.import_module(f"_lib.{module_name}")
