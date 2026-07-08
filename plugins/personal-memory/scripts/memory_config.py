from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_PROFILE = "default"
DEFAULT_HOME = Path.home() / ".personal-memory"


@dataclass(frozen=True)
class MemoryProfile:
    name: str
    memory_root: Path
    db_path: Path
    config_path: Path | None = None


class ProfileError(Exception):
    pass


def personal_memory_home() -> Path:
    return Path(os.environ.get("PERSONAL_MEMORY_HOME", DEFAULT_HOME)).expanduser()


def profile_path(profile: str = DEFAULT_PROFILE) -> Path:
    return personal_memory_home() / "profiles" / f"{profile}.json"


def load_profile(
    profile: str = DEFAULT_PROFILE,
    *,
    memory_root: Path | None = None,
    db_path: Path | None = None,
    fallback_root: Path | None = None,
) -> MemoryProfile:
    if memory_root is not None:
        root = memory_root.expanduser().resolve()
        return MemoryProfile(profile, root, resolve_db_path(root, db_path), None)

    path = profile_path(profile)
    if path.exists():
        data = read_profile_json(path)
        root_value = data.get("memory_root")
        if not root_value:
            raise ProfileError(f"profile {profile!r} at {path} missing memory_root")
        root = Path(str(root_value)).expanduser().resolve()
        configured_db = Path(str(data["db_path"])).expanduser() if data.get("db_path") else db_path
        return MemoryProfile(profile, root, resolve_db_path(root, configured_db), path)

    if fallback_root is not None:
        root = fallback_root.expanduser().resolve()
        return MemoryProfile(profile, root, resolve_db_path(root, db_path), None)

    raise ProfileError(f"profile {profile!r} not found at {path}")


def resolve_db_path(memory_root: Path, db_path: Path | None) -> Path:
    return (db_path.expanduser().resolve() if db_path else memory_root / "memory_graph.db")


def read_profile_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ProfileError(f"invalid profile JSON at {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ProfileError(f"profile JSON at {path} must be an object")
    return data
