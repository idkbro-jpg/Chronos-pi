"""
Small runtime helpers used by bot.py.

Kept separate so core safety behaviour can be updated without rewriting
the entire Discord command surface in one commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional, Set


PLACEHOLDER_UID = 123456789012345678


def allowed_user_ids(raw, placeholder_uid: int = PLACEHOLDER_UID) -> Set[int]:
    out: Set[int] = set()
    placeholders = {int(placeholder_uid), 0}
    for x in raw or []:
        try:
            uid = int(x)
        except (TypeError, ValueError):
            continue
        if uid in placeholders:
            continue
        out.add(uid)
    return out


def run_max_chunks(raw_limits: Optional[dict], default: int = 4) -> int:
    try:
        n = int((raw_limits or {}).get("run_max_chunks") or default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, 10))


def normalize_user_path(target: Path) -> Path:
    try:
        return Path(target).expanduser()
    except Exception:
        return Path(target)


def mix_aliases(mix: Any) -> Dict[str, Any]:
    aliases = mix.get("aliases") if isinstance(mix, dict) else None
    return aliases if isinstance(aliases, dict) else {}


def should_init_testmode(already_initialized: bool) -> bool:
    """on_ready fires on reconnect; only apply testmode_default once per process."""
    return not already_initialized
