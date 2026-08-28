#!/usr/bin/env python3
"""
Chronos-pi – Raspberry Pi companion bot for the Chronos ecosystem.

Self-contained. Matches the README feature set:
  - TESTMODE (default on) vs LIVE
  - Privileged commands gated by allowed_users + rate limit + TESTMODE
  - Safe path handling for echo/serve
  - Temporary HTTP servers with auto-stop + manual stop
  - Wake-on-LAN
  - Pi-aware sysinfo
  - Bridge channel support (ack-only, never auto-execute)
  - Optional allowlist for `run` (default: unrestricted)
  - Daily human logs + structured JSONL events
  - Config / mix reload without restart

Never commit a real .env / token.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional, Set, Tuple

import discord
import yaml
from discord.ext import commands
from dotenv import load_dotenv

import pi_runtime

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
MIX_PATH = ROOT / "mix.yml"
LOGS_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"

VERSION = "1.1.4"

PLACEHOLDER_UID = 123456789012345678
PLACEHOLDER_CHANNEL = 1111111111111111111

_log_lock = threading.Lock()


def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"chronos-pi-{day}.log"
    logger = logging.getLogger("chronos-pi")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)
    return logger


log = _setup_logging()


def log_event(kind: str, *, user_id: Optional[int] = None, user_name: str = "", detail: str = "", extra: Optional[dict] = None) -> None:
    record = {"ts": datetime.now(timezone.utc).isoformat(), "kind": kind, "user_id": user_id, "user_name": user_name, "detail": (detail or "")[:2000]}
    if extra:
        record["extra"] = extra
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = LOGS_DIR / f"chronos-pi-events-{day}.jsonl"
        line = json.dumps(record, ensure_ascii=False) + "\n"
        with _log_lock:
            with open(path, "a", encoding="utf-8") as f:
                f.write(line)
                f.flush()
    except Exception as e:
        log.warning("Failed to write structured log: %s", e)
    uid_s = f" uid={user_id}" if user_id is not None else ""
    log.info("[%s]%s %s %s", kind, uid_s, user_name, detail[:300] if detail else "")
