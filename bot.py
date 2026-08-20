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

# ---------------------------------------------------------------------------
# Paths & constants
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yml"
MIX_PATH = ROOT / "mix.yml"
LOGS_DIR = ROOT / "logs"
ENV_PATH = ROOT / ".env"

VERSION = "1.1.1"

PLACEHOLDER_UID = 123456789012345678
PLACEHOLDER_CHANNEL = 1111111111111111111

# ---------------------------------------------------------------------------
# Logging (daily human files + separate structured JSONL)
# ---------------------------------------------------------------------------

def _setup_logging() -> logging.Logger:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_file = LOGS_DIR / f"chronos-pi-{day}.log"

    logger = logging.getLogger("chronos-pi")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    return logger


log = _setup_logging()


def log_event(
    kind: str,
    *,
    user_id: Optional[int] = None,
    user_name: str = "",
    detail: str = "",
    extra: Optional[dict] = None,
) -> None:
    """Structured JSON line for easy grepping / later analysis (separate file)."""
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "kind": kind,
        "user_id": user_id,
        "user_name": user_name,
        "detail": (detail or "")[:2000],
    }
    if extra:
        record["extra"] = extra
    try:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        path = LOGS_DIR / f"chronos-pi-events-{day}.jsonl"
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as e:
        log.warning("Failed to write structured log: %s", e)
    # human-readable console / file line
    uid_s = f" uid={user_id}" if user_id is not None else ""
    log.info("[%s]%s %s %s", kind, uid_s, user_name, detail[:300] if detail else "")


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_cfg: Dict[str, Any] = {}
_mix: Dict[str, Any] = {}


def _defaults() -> Dict[str, Any]:
    return {
        "bot": {
            "name": "Chronos-pi",
            "prefix": "pi!",
            "status": "watching the Pi",
        },
        "testmode_default": True,
        "allowed_users": [],
        "channels": {
            "main": 0,
            "bridge": 0,
        },
        "bridge_marker": "[CHRONOS]",
        "mix_file": "mix.yml",
        "wol": {
            "default_mac": "",
        },
        "safe_serve_dirs": [],
        "limits": {
            "run_timeout_sec": 60,
            "run_max_output": 1800,
            "serve_max_minutes": 60,
            "serve_max_concurrent": 5,
            "echo_max_chars": 100000,
        },
        "rate_limit": {
            "max_commands": 15,
            "window_seconds": 60,
        },
        "execution": {
            # "unrestricted" (default) or "allowlist"
            "mode": "unrestricted",
            # patterns: plain exact, glob (* ?), or re:REGEX  (same rules as main Chronos)
            "allowed_patterns": [],
        },
    }


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_config() -> Dict[str, Any]:
    global _cfg
    cfg = _defaults()
    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            if isinstance(data, dict):
                cfg = _deep_merge(cfg, data)
            log.info("Loaded config.yml")
        except Exception as e:
            log.error("Failed to load config.yml: %s – using defaults", e)
    else:
        log.warning("No config.yml found – using defaults")
    _cfg = cfg
    return _cfg


def load_mix() -> Dict[str, Any]:
    global _mix
    mix_name = str(_cfg.get("mix_file") or "mix.yml")
    path = Path(mix_name)
    if not path.is_absolute():
        path = ROOT / path
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            _mix = data if isinstance(data, dict) else {}
            log.info("Loaded mix file: %s", path.name)
        except Exception as e:
            log.warning("Failed to load mix file: %s", e)
            _mix = {}
    else:
        _mix = {}
    return _mix


def get_cfg() -> Dict[str, Any]:
    return _cfg or load_config()


def prefix() -> str:
    return str(get_cfg().get("bot", {}).get("prefix") or "pi!")


def allowed_user_ids() -> Set[int]:
    raw = get_cfg().get("allowed_users") or []
    out: Set[int] = set()
    for x in raw:
        try:
            out.add(int(x))
        except (TypeError, ValueError):
            continue
    return out


def channel_id(name: str) -> int:
    try:
        return int((get_cfg().get("channels") or {}).get(name) or 0)
    except (TypeError, ValueError):
        return 0


def bridge_marker() -> str:
    return str(get_cfg().get("bridge_marker") or "[CHRONOS]")


def run_timeout() -> int:
    return int((get_cfg().get("limits") or {}).get("run_timeout_sec") or 60)


def run_max_output() -> int:
    return int((get_cfg().get("limits") or {}).get("run_max_output") or 1800)


def serve_max_minutes() -> int:
    return int((get_cfg().get("limits") or {}).get("serve_max_minutes") or 60)


def serve_max_concurrent() -> int:
    return int((get_cfg().get("limits") or {}).get("serve_max_concurrent") or 5)


def echo_max_chars() -> int:
    return int((get_cfg().get("limits") or {}).get("echo_max_chars") or 100000)


def rate_limit_max() -> int:
    return int((get_cfg().get("rate_limit") or {}).get("max_commands") or 15)


def rate_limit_window() -> int:
    return int((get_cfg().get("rate_limit") or {}).get("window_seconds") or 60)


def default_mac() -> str:
    return str((get_cfg().get("wol") or {}).get("default_mac") or "").strip()


def safe_serve_dirs() -> List[Path]:
    raw = get_cfg().get("safe_serve_dirs") or []
    out: List[Path] = []
    for p in raw:
        try:
            out.append(Path(p).resolve())
        except Exception:
            continue
    return out


def execution_mode() -> str:
    mode = str((get_cfg().get("execution") or {}).get("mode") or "unrestricted").lower().strip()
    if mode not in ("unrestricted", "allowlist"):
        return "unrestricted"
    return mode


def allowed_patterns() -> List[str]:
    pats = (get_cfg().get("execution") or {}).get("allowed_patterns") or []
    return [str(p) for p in pats if p]


# ---------------------------------------------------------------------------
# Runtime state
# ---------------------------------------------------------------------------

testmode: bool = True  # set from config on startup
_rate_hits: Dict[int, Deque[float]] = defaultdict(deque)
_active_servers: Dict[int, dict] = {}  # port -> metadata
_server_lock = threading.Lock()
_shutting_down = False


def is_allowed(user_id: int) -> bool:
    allowed = allowed_user_ids()
    if not allowed:
        # empty list → deny privileged (safe default)
        return False
    return user_id in allowed


def check_rate_limit(user_id: int) -> Tuple[bool, int]:
    """Returns (allowed, retry_after_seconds)."""
    now = time.time()
    window = rate_limit_window()
    limit = rate_limit_max()
    q = _rate_hits[user_id]
    while q and now - q[0] > window:
        q.popleft()
    if len(q) >= limit:
        oldest = q[0] if q else now
        retry = max(1, int(window - (now - oldest)) + 1)
        return False, retry
    q.append(now)
    return True, 0


def command_allowed_by_policy(command: str) -> Tuple[bool, str]:
    """
    When execution.mode is allowlist, only commands matching allowed_patterns may run.

    Pattern kinds (same as main Chronos):
      - plain string without * or ?  → exact full-string match
      - glob (* and ?)               → full-string glob match
      - re:REGEX                     → re.search on the whole command

    Intentionally no loose substring match.
    """
    mode = execution_mode()
    if mode != "allowlist":
        return True, ""

    patterns = allowed_patterns()
    if not patterns:
        return False, "allowlist mode with empty allowed_patterns – blocked"

    cmd = command.strip()
    for pat in patterns:
        if not pat:
            continue
        if pat.startswith("re:"):
            try:
                if re.search(pat[3:], cmd):
                    return True, ""
            except re.error:
                continue
            continue

        # Glob / exact: always fullmatch after translating * and ?
        try:
            regex = re.escape(pat).replace(r"\*", ".*").replace(r"\?", ".")
            if re.fullmatch(regex, cmd):
                return True, ""
        except re.error:
            continue

    return False, "command not in allowlist (mode=allowlist)"


# ---------------------------------------------------------------------------
# Helpers – safety, paths, shell, WOL, sysinfo, network
# ---------------------------------------------------------------------------

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}([0-9A-Fa-f]{2})$")


def normalize_mac(mac: str) -> Optional[str]:
    mac = mac.strip().replace("-", ":").upper()
    if not _MAC_RE.match(mac):
        return None
    return mac


def send_wol(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> Tuple[bool, str]:
    """Send a Wake-on-LAN magic packet (stdlib only)."""
    norm = normalize_mac(mac)
    if not norm:
        return False, f"Invalid MAC address: {mac!r}"
    try:
        mac_bytes = bytes.fromhex(norm.replace(":", ""))
        packet = b"\xff" * 6 + mac_bytes * 16
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            s.sendto(packet, (broadcast, port))
        return True, f"WOL packet sent to {norm}"
    except Exception as e:
        return False, f"WOL failed: {e}"


def _is_path_allowed(target: Path, extra_dirs: Optional[List[Path]] = None) -> bool:
    """Return True only if target is under /tmp, CWD, or an explicit safe dir."""
    try:
        resolved = target.resolve()
    except Exception:
        return False
    allowed_roots = [Path("/tmp").resolve(), Path.cwd().resolve()]
    if extra_dirs:
        allowed_roots.extend(extra_dirs)
    for root in allowed_roots:
        try:
            resolved.relative_to(root)
            return True
        except ValueError:
            continue
    return False


def run_shell(cmd: str, timeout: int) -> Tuple[int, str, str]:
    """
    Run a shell command with hard timeout and process-group kill.
    Returns (returncode, stdout, stderr).
    """
    env = os.environ.copy()
    env["PYTHONUNBUFFERED"] = "1"
    env["TERM"] = env.get("TERM") or "dumb"

    try:
        # start_new_session=True → new process group so we can kill the whole tree
        proc = subprocess.Popen(
            cmd,
            shell=True,
            executable="/bin/bash",
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            start_new_session=True,
        )
        try:
            stdout_b, stderr_b = proc.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                proc.kill()
            except Exception:
                pass
            try:
                proc.wait(timeout=2)
            except Exception:
                pass
            return 124, "", f"Command timed out after {timeout}s (killed)"
        stdout = (stdout_b or b"").decode("utf-8", errors="replace")
        stderr = (stderr_b or b"").decode("utf-8", errors="replace")
        return proc.returncode if proc.returncode is not None else 1, stdout, stderr
    except Exception as e:
        return 1, "", f"Execution error: {e}"


def _read_file_safe(path: Path, max_bytes: int = 4096) -> str:
    try:
        if not path.is_file():
            return ""
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace").strip()
    except Exception:
        return ""


def get_local_ips() -> List[str]:
    """Best-effort non-loopback IPv4 addresses for this host (useful for serve URLs)."""
    ips: List[str] = []
    try:
        # Primary: connect trick (doesn't actually send packets)
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if ip and not ip.startswith("127."):
                ips.append(ip)
        finally:
            s.close()
    except Exception:
        pass
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if ip and not ip.startswith("127.") and ip not in ips:
                ips.append(ip)
    except Exception:
        pass
    return ips


def gather_sysinfo() -> str:
    """Pi-aware system summary (read-only)."""
    lines: List[str] = []

    # Hostname / platform
    try:
        import platform
        lines.append(f"**Host:** `{socket.gethostname()}` ({platform.machine()} / {platform.system()})")
    except Exception:
        lines.append(f"**Host:** `{socket.gethostname()}`")

    # Uptime
    try:
        with open("/proc/uptime", "r") as f:
            up = float(f.read().split()[0])
        days, rem = divmod(int(up), 86400)
        hours, rem = divmod(rem, 3600)
        mins, secs = divmod(rem, 60)
        lines.append(f"**Uptime:** {days}d {hours}h {mins}m {secs}s")
    except Exception:
        pass

    # CPU temp (Pi + many SBCs)
    temp = _read_file_safe(Path("/sys/class/thermal/thermal_zone0/temp"))
    if temp.isdigit():
        t = int(temp) / 1000.0
        lines.append(f"**Temp:** `{t:.1f} °C`")
    else:
        # fallback
        for p in Path("/sys/class/thermal").glob("thermal_zone*/temp"):
            val = _read_file_safe(p)
            if val.isdigit():
                lines.append(f"**Temp ({p.parent.name}):** `{int(val)/1000:.1f} °C`")
                break

    # Throttled flags (Raspberry Pi)
    try:
        out = subprocess.check_output(
            ["vcgencmd", "get_throttled"],
            stderr=subprocess.DEVNULL,
            timeout=2,
            text=True,
        ).strip()
        if out:
            lines.append(f"**Throttled:** `{out}`")
    except Exception:
        pass

    # Memory
    try:
        mem = {}
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if ":" in line:
                    k, v = line.split(":", 1)
                    mem[k.strip()] = v.strip()
        total = int(mem.get("MemTotal", "0").split()[0])
        avail = int(mem.get("MemAvailable", mem.get("MemFree", "0")).split()[0])
        used = total - avail
        lines.append(
            f"**Mem:** `{used/1024:.0f} / {total/1024:.0f} MiB` "
            f"({100*used/total:.0f}% used)" if total else "**Mem:** n/a"
        )
    except Exception:
        pass

    # Disk (root)
    try:
        st = os.statvfs("/")
        total = st.f_blocks * st.f_frsize
        free = st.f_bavail * st.f_frsize
        used = total - free
        lines.append(
            f"**Disk (/):** `{used/1e9:.1f} / {total/1e9:.1f} GB` "
            f"({100*used/total:.0f}% used)"
        )
    except Exception:
        pass

    # Load + CPU cores
    try:
        load1, load5, load15 = os.getloadavg()
        cores = os.cpu_count() or "?"
        lines.append(f"**Load:** `{load1:.2f} {load5:.2f} {load15:.2f}` ({cores} cores)")
    except Exception:
        pass

    # Local IPs (handy for serve)
    ips = get_local_ips()
    if ips:
        lines.append(f"**LAN IP(s):** `{', '.join(ips)}`")

    return "\n".join(lines) if lines else "_Could not collect sysinfo_"


# ---------------------------------------------------------------------------
# Temporary HTTP servers
# ---------------------------------------------------------------------------

class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:  # noqa: A003
        # keep noise low; still log errors via main logger if needed
        pass


def _start_http_server(directory: Path, port: int, minutes: int) -> Tuple[bool, str]:
    try:
        directory = directory.resolve()
    except Exception as e:
        return False, f"Invalid path: {e}"
    if not directory.is_dir():
        return False, f"Not a directory: {directory}"
    if not _is_path_allowed(directory, safe_serve_dirs()):
        return False, (
            "Path not allowed. Only `/tmp`, CWD and configured "
            "`safe_serve_dirs` are permitted."
        )
    if minutes < 1 or minutes > serve_max_minutes():
        return False, f"Minutes must be 1–{serve_max_minutes()}"
    if port < 1024 or port > 65535:
        return False, "Port must be in 1024–65535 (non-privileged)"

    with _server_lock:
        if port in _active_servers:
            return False, f"Port {port} already in use by a temp server"
        if len(_active_servers) >= serve_max_concurrent():
            return False, (
                f"Max concurrent temp servers reached "
                f"({serve_max_concurrent()}). Stop one first."
            )

    # Handler that serves a specific directory without process-wide chdir
    class DirHandler(QuietHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(directory), **kwargs)

    try:
        # 0.0.0.0 so other devices on the LAN can reach the Pi
        server = ThreadingHTTPServer(("0.0.0.0", port), DirHandler)
    except OSError as e:
        return False, f"Cannot bind port {port}: {e}"

    stop_at = time.time() + minutes * 60
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    with _server_lock:
        _active_servers[port] = {
            "server": server,
            "thread": thread,
            "directory": str(directory),
            "started": time.time(),
            "stop_at": stop_at,
            "minutes": minutes,
        }

    def _auto_stop():
        remaining = stop_at - time.time()
        if remaining > 0:
            time.sleep(remaining)
        _stop_http_server(port, reason="auto-timeout")

    threading.Thread(target=_auto_stop, daemon=True).start()

    ips = get_local_ips()
    urls = [f"http://{ip}:{port}/" for ip in ips] if ips else [f"http://<pi-ip>:{port}/"]
    url_line = " · ".join(f"`{u}`" for u in urls)
    return True, (
        f"Serving `{directory}` on port **{port}** for {minutes} min\n"
        f"Access: {url_line}"
    )


def _stop_http_server(port: int, reason: str = "manual") -> bool:
    with _server_lock:
        meta = _active_servers.pop(port, None)
    if not meta:
        return False
    try:
        meta["server"].shutdown()
        meta["server"].server_close()
    except Exception as e:
        log.warning("Error shutting down server on %s: %s", port, e)
    log.info("Temp HTTP server port=%s stopped (%s)", port, reason)
    return True


def stop_all_servers() -> int:
    with _server_lock:
        ports = list(_active_servers.keys())
    count = 0
    for p in ports:
        if _stop_http_server(p, reason="shutdown"):
            count += 1
    return count


def list_servers() -> str:
    with _server_lock:
        if not _active_servers:
            return "_No active temporary HTTP servers._"
        lines = ["**Active temp HTTP servers:**"]
        now = time.time()
        for port, meta in sorted(_active_servers.items()):
            left = max(0, int(meta["stop_at"] - now))
            lines.append(
                f"• port `{port}` → `{meta['directory']}` "
                f"(~{left}s left, started {meta['minutes']} min window)"
            )
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=lambda b, m: prefix(), intents=intents, help_command=None)


def _chunk(text: str, limit: int = 1900) -> List[str]:
    if not text:
        return []
    return [text[i : i + limit] for i in range(0, len(text), limit)]


def _safe_code(text: str) -> str:
    return (text or "").replace("```", "``\u200b`")


async def _reply(ctx_or_msg, content: str) -> None:
    """Reply that works for both Context and Message."""
    if isinstance(ctx_or_msg, commands.Context):
        await ctx_or_msg.reply(content[:2000])
    else:
        await ctx_or_msg.reply(content[:2000])


async def _testmode_gate(ctx: commands.Context, action_desc: str) -> bool:
    """
    If TESTMODE is on → announce and return False (do not execute).
    If LIVE → return True (caller may execute).
    """
    global testmode
    if testmode:
        await ctx.reply(f"🧪 TESTMODE → Ich würde jetzt **{action_desc}** machen.")
        log_event(
            "testmode_block",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=action_desc,
        )
        return False
    return True


def privileged():
    """Decorator: only allowed_users + rate-limit."""

    async def predicate(ctx: commands.Context) -> bool:
        if not is_allowed(ctx.author.id):
            await ctx.reply("⛔ Not in `allowed_users`.")
            log_event(
                "denied",
                user_id=ctx.author.id,
                user_name=str(ctx.author),
                detail=ctx.message.content[:200],
            )
            return False
        ok, retry = check_rate_limit(ctx.author.id)
        if not ok:
            await ctx.reply(f"⏳ Rate limit. Try again in ~**{retry}s**.")
            log_event(
                "rate_limited",
                user_id=ctx.author.id,
                user_name=str(ctx.author),
                detail=ctx.message.content[:200],
            )
            return False
        return True

    return commands.check(predicate)


# ---------- commands ----------

@bot.command(name="help")
async def cmd_help(ctx: commands.Context):
    p = prefix()
    text = (
        f"**Chronos-pi help** v{VERSION} (prefix `{p}`)\n"
        f"```\n"
        f"{p}help                 this message\n"
        f"{p}ping                 latency\n"
        f"{p}status               mode + servers + uptime\n"
        f"{p}sysinfo              host / temp / mem / disk / IPs\n"
        f"{p}whoami               your Discord user id\n"
        f"{p}bridge <text>        send to bridge channel\n"
        f"{p}TESTMODE [on|off]    toggle test mode\n"
        f"{p}reload               reload config.yml + mix.yml\n"
        f"{p}wol [mac]            Wake-on-LAN (LIVE only)\n"
        f"{p}run <cmd>            shell command (LIVE only)\n"
        f"{p}echo <file> <text>   write file (LIVE only, safe dirs)\n"
        f"{p}serve [dir] [port] [min]  temp HTTP server (LIVE only)\n"
        f"{p}stopserve [port]     stop a temp HTTP server (LIVE only)\n"
        f"{p}servestatus          list active temp servers\n"
        f"```\n"
        f"_Privileged actions are blocked while TESTMODE is on._"
    )
    await ctx.reply(text)


@bot.command(name="ping")
async def cmd_ping(ctx: commands.Context):
    latency_ms = round(bot.latency * 1000)
    await ctx.reply(f"Pong · `{latency_ms} ms`")


@bot.command(name="status")
async def cmd_status(ctx: commands.Context):
    mode = "🧪 TESTMODE" if testmode else "🔴 LIVE"
    servers = 0
    with _server_lock:
        servers = len(_active_servers)
    allowed_n = len(allowed_user_ids())
    exec_mode = execution_mode()
    await ctx.reply(
        f"**Chronos-pi status** v{VERSION}\n"
        f"mode: **{mode}**\n"
        f"execution: `{exec_mode}`\n"
        f"allowed_users: `{allowed_n}`\n"
        f"active temp servers: `{servers}` / `{serve_max_concurrent()}`\n"
        f"guilds: `{len(bot.guilds)}`"
    )


@bot.command(name="sysinfo")
async def cmd_sysinfo(ctx: commands.Context):
    info = await asyncio.to_thread(gather_sysinfo)
    await ctx.reply(info)


@bot.command(name="whoami")
async def cmd_whoami(ctx: commands.Context):
    await ctx.reply(f"Your Discord user id: `{ctx.author.id}`")


@bot.command(name="bridge")
@privileged()
async def cmd_bridge(ctx: commands.Context, *, text: str = ""):
    if not text.strip():
        await ctx.reply("Usage: `pi!bridge <message>`")
        return
    cid = channel_id("bridge")
    if not cid:
        await ctx.reply("Bridge channel not configured.")
        return
    channel = bot.get_channel(cid)
    if channel is None:
        try:
            channel = await bot.fetch_channel(cid)
        except Exception as e:
            await ctx.reply(f"Cannot reach bridge channel: {e}")
            return
    marker = bridge_marker()
    payload = f"{marker} {text.strip()}"
    try:
        await channel.send(payload[:1900])
        await ctx.reply("Sent to bridge.")
        log_event(
            "bridge_send",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=text[:300],
        )
    except Exception as e:
        await ctx.reply(f"Bridge send failed: {e}")


@bot.command(name="TESTMODE")
@privileged()
async def cmd_testmode(ctx: commands.Context, state: Optional[str] = None):
    global testmode
    if state is None:
        testmode = not testmode
    else:
        s = state.strip().lower()
        if s in ("on", "1", "true", "yes"):
            testmode = True
        elif s in ("off", "0", "false", "no"):
            testmode = False
        else:
            await ctx.reply("Usage: `pi!TESTMODE [on|off]`")
            return
    mode = "🧪 TESTMODE **ON**" if testmode else "🔴 LIVE **MODE**"
    await ctx.reply(f"Switched → {mode}")
    log_event(
        "testmode_toggle",
        user_id=ctx.author.id,
        user_name=str(ctx.author),
        detail="on" if testmode else "off",
    )


@bot.command(name="reload")
@privileged()
async def cmd_reload(ctx: commands.Context):
    """Reload config.yml and mix.yml without restarting the bot."""
    load_config()
    load_mix()
    # Note: testmode is intentionally NOT reset – only changes via TESTMODE command
    # or next process start (testmode_default).
    await ctx.reply(
        f"✅ Reloaded config + mix.\n"
        f"execution.mode=`{execution_mode()}` · "
        f"allowed_users=`{len(allowed_user_ids())}` · "
        f"serve_max_concurrent=`{serve_max_concurrent()}`"
    )
    log_event(
        "reload",
        user_id=ctx.author.id,
        user_name=str(ctx.author),
        detail=f"mode={execution_mode()}",
    )


@bot.command(name="wol")
@privileged()
async def cmd_wol(ctx: commands.Context, mac: Optional[str] = None):
    target = (mac or default_mac() or "").strip()
    if not target:
        await ctx.reply("Usage: `pi!wol <mac>` or set `wol.default_mac` in config.yml")
        return
    if not await _testmode_gate(ctx, f"WOL an {target}"):
        return
    ok, msg = await asyncio.to_thread(send_wol, target)
    await ctx.reply(("✅ " if ok else "❌ ") + msg)
    log_event(
        "wol",
        user_id=ctx.author.id,
        user_name=str(ctx.author),
        detail=target,
        extra={"ok": ok},
    )


@bot.command(name="run")
@privileged()
async def cmd_run(ctx: commands.Context, *, command: str = ""):
    command = command.strip()
    if not command:
        await ctx.reply("Usage: `pi!run <shell command>`")
        return
    if not await _testmode_gate(ctx, f"run `{command[:80]}`"):
        return

    allowed, reason = command_allowed_by_policy(command)
    if not allowed:
        await ctx.reply(f"⛔ Policy: {reason}")
        log_event(
            "run_denied_policy",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=command[:300],
            extra={"reason": reason},
        )
        return

    timeout = run_timeout()
    max_out = run_max_output()
    await ctx.reply(f"⚙️ Running (timeout {timeout}s)…")
    rc, stdout, stderr = await asyncio.to_thread(run_shell, command, timeout)

    log_event(
        "run",
        user_id=ctx.author.id,
        user_name=str(ctx.author),
        detail=command[:500],
        extra={"returncode": rc},
    )

    await ctx.reply(f"**Exit code:** `{rc}`")
    if stdout:
        for i, chunk in enumerate(_chunk(stdout, max_out)):
            label = "**stdout:**" if i == 0 else f"**stdout (cont. {i+1}):**"
            await ctx.reply(f"{label}\n```\n{_safe_code(chunk)}\n```")
    if stderr:
        for i, chunk in enumerate(_chunk(stderr, max_out)):
            label = "**stderr:**" if i == 0 else f"**stderr (cont. {i+1}):**"
            await ctx.reply(f"{label}\n```\n{_safe_code(chunk)}\n```")
    if not stdout and not stderr:
        await ctx.reply("_No output_")


@bot.command(name="echo")
@privileged()
async def cmd_echo(ctx: commands.Context, path: str = "", *, text: str = ""):
    if not path or not text:
        await ctx.reply("Usage: `pi!echo <file> <text>`")
        return
    target = Path(path)
    if not await _testmode_gate(ctx, f"echo into `{target}`"):
        return

    if len(text) > echo_max_chars():
        await ctx.reply(
            f"⛔ Text too long ({len(text)} chars). "
            f"Limit is {echo_max_chars()} (config `limits.echo_max_chars`)."
        )
        return

    if not _is_path_allowed(target, safe_serve_dirs()):
        await ctx.reply(
            "⛔ Path not allowed. Only `/tmp`, CWD and `safe_serve_dirs` are permitted."
        )
        return

    try:
        # Write to the resolved path so relative components / .. cannot surprise us
        resolved = target.resolve()
        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(text, encoding="utf-8")
        await ctx.reply(f"✅ Wrote {len(text)} bytes to `{resolved}`")
        log_event(
            "echo",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=str(resolved),
            extra={"bytes": len(text)},
        )
    except Exception as e:
        await ctx.reply(f"❌ Write failed: {e}")


@bot.command(name="serve")
@privileged()
async def cmd_serve(
    ctx: commands.Context,
    folder: str = ".",
    port: int = 8080,
    minutes: int = 15,
):
    if not await _testmode_gate(ctx, f"serve `{folder}` on port {port} for {minutes} min"):
        return

    directory = Path(folder)
    ok, msg = await asyncio.to_thread(_start_http_server, directory, port, minutes)
    await ctx.reply(("✅ " if ok else "❌ ") + msg)
    if ok:
        log_event(
            "serve_start",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=f"{directory} :{port} {minutes}min",
        )


@bot.command(name="stopserve")
@privileged()
async def cmd_stopserve(ctx: commands.Context, port: Optional[int] = None):
    if port is None:
        await ctx.reply("Usage: `pi!stopserve <port>`  (see `pi!servestatus`)")
        return
    if not await _testmode_gate(ctx, f"stopserve port {port}"):
        return
    ok = await asyncio.to_thread(_stop_http_server, port, "manual")
    if ok:
        await ctx.reply(f"✅ Stopped temp server on port `{port}`")
        log_event(
            "serve_stop",
            user_id=ctx.author.id,
            user_name=str(ctx.author),
            detail=str(port),
        )
    else:
        await ctx.reply(f"❌ No active temp server on port `{port}`")


@bot.command(name="servestatus")
async def cmd_servestatus(ctx: commands.Context):
    await ctx.reply(list_servers())


# ---------- events ----------

@bot.event
async def on_ready():
    global testmode
    load_config()
    load_mix()
    testmode = bool(get_cfg().get("testmode_default", True))

    # Startup warnings for placeholders
    allowed = allowed_user_ids()
    if not allowed or PLACEHOLDER_UID in allowed:
        log.warning(
            "⚠️  allowed_users still contains the placeholder ID or is empty. "
            "Privileged commands will be denied until you put your real Discord user id."
        )
    for name in ("main", "bridge"):
        cid = channel_id(name)
        if not cid or cid == PLACEHOLDER_CHANNEL or cid == 2222222222222222222:
            log.warning("⚠️  channels.%s looks like a placeholder (%s)", name, cid)

    if execution_mode() == "allowlist" and not allowed_patterns():
        log.warning(
            "⚠️  execution.mode=allowlist but allowed_patterns is empty – "
            "all `run` commands will be blocked."
        )

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=str(get_cfg().get("bot", {}).get("status") or "the Pi"),
    )
    await bot.change_presence(activity=activity)

    log.info(
        "Logged in as %s (ID %s) | v%s | TESTMODE=%s | prefix=%s | exec=%s",
        bot.user,
        bot.user.id if bot.user else "?",
        VERSION,
        testmode,
        prefix(),
        execution_mode(),
    )
    log_event("startup", detail=f"v{VERSION} testmode={testmode} exec={execution_mode()}")


@bot.event
async def on_message(message: discord.Message):
    if _shutting_down:
        return
    if message.author.bot:
        return

    # Bridge channel: simple ack protocol (never auto-execute)
    bridge_cid = channel_id("bridge")
    if bridge_cid and message.channel.id == bridge_cid:
        marker = bridge_marker()
        content = message.content.strip()
        if content.startswith(marker):
            body = content[len(marker) :].strip()
            low = body.lower()
            log_event(
                "bridge_recv",
                user_id=message.author.id,
                user_name=str(message.author),
                detail=body[:300],
            )
            if low.startswith("do "):
                # security: only acknowledge, never run
                try:
                    await message.add_reaction("👀")
                    await message.reply(
                        f"Bridge `do` received and **acknowledged only** "
                        f"(not executed): `{body[3:80]}`"
                    )
                except Exception:
                    pass
            elif low in ("ping", "status"):
                try:
                    await message.reply(
                        f"Chronos-pi v{VERSION} · TESTMODE={'on' if testmode else 'off'}"
                    )
                except Exception:
                    pass
        return  # do not process prefix commands in bridge channel

    await bot.process_commands(message)


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    if isinstance(error, commands.CheckFailure):
        return  # already answered by the check
    if isinstance(error, commands.CommandNotFound):
        return
    log.exception("Command error in %s: %s", ctx.command, error)
    try:
        await ctx.reply(f"⚠️ Error: `{type(error).__name__}: {error}`")
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Main / graceful shutdown
# ---------------------------------------------------------------------------

async def _close_gracefully() -> None:
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    log.info("Shutting down…")
    n = stop_all_servers()
    log.info("Stopped %s temp HTTP server(s)", n)
    log_event("shutdown", detail=f"servers_stopped={n}")
    try:
        await bot.close()
    except Exception:
        pass


def main() -> None:
    load_dotenv(ENV_PATH)
    token = os.getenv("DISCORD_TOKEN", "").strip()
    if not token or token == "dein_bot_token_hier":
        log.error("DISCORD_TOKEN missing or still the example value. Set it in .env")
        sys.exit(1)

    load_config()

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    def _handle_sig(*_args):
        log.info("Signal received")
        loop.call_soon_threadsafe(lambda: asyncio.ensure_future(_close_gracefully()))

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, _handle_sig)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_sig())

    try:
        loop.run_until_complete(bot.start(token))
    except KeyboardInterrupt:
        loop.run_until_complete(_close_gracefully())
    finally:
        try:
            loop.run_until_complete(bot.close())
        except Exception:
            pass
        loop.close()
        stop_all_servers()  # belt-and-suspenders


if __name__ == "__main__":
    main()
