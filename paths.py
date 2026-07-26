"""Portable paths derived from the checkout, never from a machine pathname."""
from __future__ import annotations

import os
from pathlib import Path


def _configured_path(name: str, default: Path) -> Path:
    value = os.environ.get(name, "").strip()
    return Path(value).expanduser() if value else default


RECAST_ROOT = _configured_path("RECAST_ROOT", Path(__file__).parent).resolve()
REPO_ROOT = _configured_path("RECAST_REPO_ROOT", RECAST_ROOT.parent).resolve()
DATA_ROOT = _configured_path("RECAST_DATA_ROOT", REPO_ROOT / "STALE").resolve()
RUNS_ROOT = _configured_path("RECAST_RUNS_ROOT", RECAST_ROOT / "runs").resolve()
EMBEDDING_MODEL_ROOT = _configured_path(
    "RECAST_EMBEDDING_MODEL", RECAST_ROOT / "models" / "all-MiniLM-L6-v2"
).resolve()


def project_path(*parts: str) -> Path:
    return RECAST_ROOT.joinpath(*parts)


def repo_path(*parts: str) -> Path:
    return REPO_ROOT.joinpath(*parts)


def resolve_user_path(value: str | os.PathLike[str], *, default: Path | None = None) -> Path:
    """Resolve a CLI path without requiring a machine-specific absolute path.

    Absolute arguments are honored. Relative arguments are first interpreted
    from the caller's working directory, then from the repository and project
    roots; this keeps both ``python -m ...`` and direct script invocation useful.
    """
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    choices = [Path.cwd() / candidate, REPO_ROOT / candidate, RECAST_ROOT / candidate]
    for path in choices:
        if path.exists():
            return path
    if default is not None:
        return default if default.is_absolute() else REPO_ROOT / default
    return REPO_ROOT / candidate
