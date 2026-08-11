#!/usr/bin/env python3
"""
Chronos-pi – Discord bot companion for Raspberry Pi.
Designed to integrate with the main Chronos ecosystem via bridge channel.
TESTMODE is ON by default – real actions only run when explicitly disabled.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import socket
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional, Awaitable, Any

import discord
import yaml
from discord.ext import commands
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------
load_dotenv()

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(LOG_DIR / f"chronos-pi-{datetime.now():%Y-%m-%d}.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("chronos-pi")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
def load_config(path: str = "config.yml") -> dict:
    cfg_path = Path(path)
    if not cfg_path.is_file():
        raise FileNotFoundError(f"Config not found: {cfg_path.resolve()}")
    with cfg_path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data


config = load_config()

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN missing in .env – copy .env.example and fill it.")

ALLOWED_USERS: set[int] = set(int(u) for u in config.get("allowed_users", []) if str(u).isdigit())
if not ALLOWED_USERS:
    log.warning("allowed_users is empty – no one can run privileged commands until you add IDs in config.yml")

channels = config.get("channels") or {}
BRIDGE_CHANNEL_ID: int = int(channels.get("bridge") or 0)
MAIN_CHANNEL_ID: int = int(channels.get("main") or 0)
BRIDGE_MARKER: str = config.get("bridge_marker", "[CHRONOS]")
BOT_NAME: str = (config.get("bot") or {}).get("name", "Chronos-pi")
PREFIX: str = (config.get("bot") or {}).get("prefix", "pi!")

# Runtime state
TESTMODE: bool = bool(config.get("testmode_default", True))

# Safety limits
RUN_TIMEOUT_SEC = 60
RUN_MAX_OUTPUT = 1800
SAFE_WRITE_DIRS = {Path("/tmp").resolve(), Path(".").resolve()}  # echo restricted here
SERVE_MAX_MINUTES = 60

# ---------------------------------------------------------------------------
# Bot
# ---------------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents, help_command=None)


def is_allowed(user_id: int) -> bool:
    return user_id in ALLOWED_USERS


async def pi_action(
    ctx: commands.Context,
    description: str,
    real_action: Optional[Callable[[], Awaitable[Any]]] = None,
) -> bool:
    """
    Central gate for every Pi-side side-effect.
    TESTMODE → only announce what would happen.
    LIVE     → execute and report result / error.
    Returns True if the real action ran.
    """
    global TESTMODE

    if TESTMODE:
        await ctx.send(f"🧪 **TESTMODE** → Ich würde jetzt **{description}** machen.")
        log.info("TESTMODE blocked: %s (user=%s)", description, ctx.author.id)
        return False

    await ctx.send(f"▶️ Führe aus: **{description}**")
    log.info("LIVE action: %s (user=%s)", description, ctx.author.id)

    if real_action is None:
        return True

    try:
        result = await real_action()
        if result is not None and str(result).strip():
            text = str(result)
            if len(text) > RUN_MAX_OUTPUT:
                text = text[: RUN_MAX_OUTPUT - 20] + "\n… (truncated)"
            await ctx.send(f"✅ Ergebnis:\n```\n{text}\n```")
        return True
    except Exception as exc:
        log.exception("Action failed: %s", description)
        await ctx.send(f"❌ Fehler bei der Ausführung:\n```\n{exc}\n```")
        return False


# ---------------------------------------------------------------------------
# Helpers – real implementations (only called when TESTMODE is off)
# ---------------------------------------------------------------------------
def _normalize_mac(mac: str) -> str:
    """Return colon-separated lower-case MAC or raise ValueError."""
    cleaned = re.sub(r"[^0-9A-Fa-f]", "", mac)
    if len(cleaned) != 12:
        raise ValueError(f"Invalid MAC address: {mac!r}")
    return ":".join(cleaned[i : i + 2] for i in range(0, 12, 2)).lower()


def send_wol_packet(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> str:
    """Send a classic Wake-on-LAN magic packet (stdlib only)."""
    mac_norm = _normalize_mac(mac)
    mac_bytes = bytes.fromhex(mac_norm.replace(":", ""))
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (broadcast, port))
    return f"Magic packet sent to {mac_norm} via {broadcast}:{port}"


async def run_shell(cmd: str, timeout: int = RUN_TIMEOUT_SEC) -> str:
    """
    Execute a shell command with timeout and output capture.
    Prefer shell=True only because the user deliberately asked for arbitrary cmds;
    still hard-capped by timeout and output size.
    """
    proc = await asyncio.create_subprocess_shell(
        cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=RUN_MAX_OUTPUT * 2,
    )
    try:
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        raise TimeoutError(f"Command timed out after {timeout}s: {cmd!r}")

    out = (stdout or b"").decode("utf-8", errors="replace").strip()
    rc = proc.returncode
    header = f"exit={rc}\n" if rc != 0 else ""
    return header + (out or "(no output)")


def safe_write_path(filename: str) -> Path:
    """Resolve filename and ensure it stays inside SAFE_WRITE_DIRS."""
    p = Path(filename).expanduser()
    if p.is_absolute():
        resolved = p.resolve()
    else:
        resolved = (Path.cwd() / p).resolve()

    for safe in SAFE_WRITE_DIRS:
        try:
            resolved.relative_to(safe)
            return resolved
        except ValueError:
            continue
    raise PermissionError(
        f"Write denied outside allowed directories {SAFE_WRITE_DIRS}: {resolved}"
    )


async def write_file(filename: str, text: str) -> str:
    path = safe_write_path(filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return f"Wrote {len(text)} bytes to {path}"


def collect_sysinfo() -> str:
    """Gather useful Pi / Linux status lines (best-effort)."""
    lines: list[str] = []

    # Hostname & uptime
    try:
        lines.append(f"host: {socket.gethostname()}")
    except Exception:
        pass
    try:
        with open("/proc/uptime") as f:
            up = float(f.read().split()[0])
            h, rem = divmod(int(up), 3600)
            m, s = divmod(rem, 60)
            lines.append(f"uptime: {h}h {m}m {s}s")
    except Exception:
        pass

    # Load
    try:
        load1, load5, load15 = os.getloadavg()
        lines.append(f"load: {load1:.2f} {load5:.2f} {load15:.2f}")
    except Exception:
        pass

    # Memory
    try:
        mem = {}
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(("MemTotal:", "MemAvailable:", "MemFree:")):
                    k, v, *_ = line.split()
                    mem[k.rstrip(":")] = int(v)
        if "MemTotal" in mem and "MemAvailable" in mem:
            tot = mem["MemTotal"] // 1024
            avail = mem["MemAvailable"] // 1024
            lines.append(f"mem: {avail} / {tot} MiB available")
    except Exception:
        pass

    # CPU temperature (Pi / thermal zone)
    temp = None
    for candidate in (
        "/sys/class/thermal/thermal_zone0/temp",
        "/sys/class/hwmon/hwmon0/temp1_input",
    ):
        try:
            raw = Path(candidate).read_text().strip()
            temp = int(raw) / 1000.0
            break
        except Exception:
            continue
    if temp is None:
        try:
            out = subprocess.check_output(
                ["vcgencmd", "measure_temp"], text=True, timeout=3
            )
            # temp=45.6'C
            m = re.search(r"([\d.]+)", out)
            if m:
                temp = float(m.group(1))
        except Exception:
            pass
    if temp is not None:
        lines.append(f"cpu_temp: {temp:.1f} °C")

    # Disk
    try:
        st = os.statvfs("/")
        free = (st.f_bavail * st.f_frsize) // (1024 * 1024)
        total = (st.f_blocks * st.f_frsize) // (1024 * 1024)
        lines.append(f"disk_root: {free} / {total} MiB free")
    except Exception:
        pass

    return "\n".join(lines) if lines else "sysinfo unavailable"


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------
@bot.event
async def on_ready():
    mode = "TESTMODE" if TESTMODE else "LIVE"
    log.info("%s online as %s | %s | bridge=%s", BOT_NAME, bot.user, mode, BRIDGE_CHANNEL_ID)
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=f"{mode} | the Pi",
    )
    await bot.change_presence(activity=activity)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Bridge handling (marker messages from the other Chronos instance)
    if BRIDGE_CHANNEL_ID and message.channel.id == BRIDGE_CHANNEL_ID:
        content = message.content.strip()
        if content.startswith(BRIDGE_MARKER):
            payload = content[len(BRIDGE_MARKER) :].strip()
            lower = payload.lower()
            log.info("[Bridge] received: %s", payload)

            if lower == "ping":
                await message.channel.send(f"{BRIDGE_MARKER} pong from {BOT_NAME}")
            elif lower == "status":
                mode = "TESTMODE" if TESTMODE else "LIVE"
                await message.channel.send(
                    f"{BRIDGE_MARKER} {BOT_NAME} online | {mode} | "
                    f"latency {round(bot.latency * 1000)}ms"
                )
            elif lower.startswith("do "):
                action = payload[3:].strip()
                await message.channel.send(
                    f"{BRIDGE_MARKER} {BOT_NAME} received instruction `{action}` "
                    f"(Testmode: {'ON' if TESTMODE else 'OFF'})"
                )
            # Future: more structured protocol can be added here without breaking existing clients

    await bot.process_commands(message)


# ---------------------------------------------------------------------------
# Basic commands
# ---------------------------------------------------------------------------
@bot.command(name="help")
async def help_cmd(ctx: commands.Context):
    """Command overview."""
    text = (
        f"**{BOT_NAME}** – prefix `{PREFIX}`\n"
        "```\n"
        f"{PREFIX}ping                 latency\n"
        f"{PREFIX}status               bot + mode + bridge\n"
        f"{PREFIX}sysinfo              host / temp / mem / disk\n"
        f"{PREFIX}whoami               your Discord user id\n"
        f"{PREFIX}bridge <text>        send to bridge channel\n"
        f"{PREFIX}TESTMODE [on|off]    toggle simulation mode\n"
        f"{PREFIX}wol [mac]            Wake-on-LAN\n"
        f"{PREFIX}run <cmd>            run shell command (LIVE only)\n"
        f"{PREFIX}echo <file> <text>   write file (restricted paths)\n"
        f"{PREFIX}serve [folder] [port] [min]  temporary HTTP server\n"
        "```\n"
        "🧪 **TESTMODE is ON by default** – nothing dangerous runs until you turn it off.\n"
        "Only users listed in `config.yml → allowed_users` may use privileged commands."
    )
    await ctx.send(text)


@bot.command(name="ping")
async def ping(ctx: commands.Context):
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    await ctx.send(f"Pong! `{round(bot.latency * 1000)}ms`")


@bot.command(name="status")
async def status(ctx: commands.Context):
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    mode = "🧪 **TESTMODE AN**" if TESTMODE else "🚀 **LIVE MODE**"
    await ctx.send(
        f"**{BOT_NAME}**\n"
        f"• Online als `{bot.user}`\n"
        f"• Latency: `{round(bot.latency * 1000)}ms`\n"
        f"• Modus: {mode}\n"
        f"• Bridge-Channel: `{BRIDGE_CHANNEL_ID or 'not set'}`\n"
        f"• Allowed users: `{len(ALLOWED_USERS)}`"
    )


@bot.command(name="whoami")
async def whoami(ctx: commands.Context):
    """Always available – helps the user discover their own ID for config.yml."""
    await ctx.send(f"Deine Discord User-ID: `{ctx.author.id}`")


@bot.command(name="bridge")
async def bridge_send(ctx: commands.Context, *, text: str):
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    if not BRIDGE_CHANNEL_ID:
        return await ctx.send("❌ Bridge-Channel-ID ist in config.yml nicht gesetzt.")
    channel = bot.get_channel(BRIDGE_CHANNEL_ID)
    if channel is None:
        return await ctx.send("❌ Bridge-Channel nicht gefunden (Bot hat keinen Zugriff?).")
    await channel.send(f"{BRIDGE_MARKER} {text}")
    await ctx.send(f"✅ In Bridge gesendet: `{text}`")


@bot.command(name="TESTMODE")
async def toggle_testmode(ctx: commands.Context, state: Optional[str] = None):
    """
    pi!TESTMODE          → toggle
    pi!TESTMODE on|an    → force on
    pi!TESTMODE off|aus  → force off
    """
    global TESTMODE
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    if state is None:
        TESTMODE = not TESTMODE
    else:
        s = state.lower()
        if s in ("on", "an", "true", "1"):
            TESTMODE = True
        elif s in ("off", "aus", "false", "0"):
            TESTMODE = False
        else:
            return await ctx.send("Bitte `on` / `off` (oder nichts zum Umschalten).")

    mode_text = (
        "🧪 **TESTMODE ist jetzt AN**\n→ Pi-Aktionen werden nur simuliert."
        if TESTMODE
        else "🚀 **LIVE MODE ist jetzt AN**\n→ Pi-Aktionen werden wirklich ausgeführt!"
    )
    await ctx.send(mode_text)
    log.info("TESTMODE set to %s by %s", TESTMODE, ctx.author.id)

    await bot.change_presence(
        activity=discord.Activity(
            type=discord.ActivityType.watching,
            name=f"{'TESTMODE' if TESTMODE else 'LIVE'} | the Pi",
        )
    )


# ---------------------------------------------------------------------------
# Pi-specific actions (all gated by pi_action / TESTMODE)
# ---------------------------------------------------------------------------
@bot.command(name="wol")
async def wake_on_lan(ctx: commands.Context, mac: str = "00:11:22:33:44:55"):
    """Send a Wake-on-LAN magic packet."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_wol():
        return send_wol_packet(mac)

    await pi_action(ctx, f"Wake-on-LAN an `{mac}` senden", real_wol)


@bot.command(name="run")
async def run_command(ctx: commands.Context, *, cmd: str):
    """
    Run an arbitrary shell command on the Pi.
    Only in LIVE mode. Hard timeout + output truncation.
    """
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    if not cmd.strip():
        return await ctx.send("Leerer Befehl.")

    async def real_run():
        return await run_shell(cmd)

    await pi_action(ctx, f"folgenden Befehl ausführen: `{cmd}`", real_run)


@bot.command(name="echo")
async def echo_file(
    ctx: commands.Context,
    filename: str = "hello.txt",
    *,
    text: str = "hello guys",
):
    """Write text to a file (restricted to safe directories)."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_echo():
        return await write_file(filename, text)

    await pi_action(ctx, f'`echo "{text}" > {filename}` ausführen', real_echo)


@bot.command(name="serve")
async def temp_server(
    ctx: commands.Context,
    folder: str = "/tmp/share",
    port: int = 8000,
    minutes: int = 15,
):
    """
    Start a temporary HTTP file server (python -m http.server).
    Auto-stops after `minutes` (capped). Runs in background.
    """
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    minutes = max(1, min(int(minutes), SERVE_MAX_MINUTES))
    port = int(port)
    if not (1024 <= port <= 65535):
        return await ctx.send("Port muss zwischen 1024 und 65535 liegen.")

    async def real_serve():
        path = Path(folder).expanduser().resolve()
        path.mkdir(parents=True, exist_ok=True)
        # Launch detached so the bot stays responsive
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "http.server",
            str(port),
            "--directory",
            str(path),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        async def _killer():
            await asyncio.sleep(minutes * 60)
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except asyncio.TimeoutError:
                    proc.kill()
                log.info("HTTP server on port %s auto-stopped after %s min", port, minutes)

        asyncio.create_task(_killer())
        return (
            f"HTTP server started in {path} on port {port} "
            f"for {minutes} minutes (pid {proc.pid}). "
            f"It will stop automatically."
        )

    await pi_action(
        ctx,
        f"temporären HTTP-Server in `{folder}` auf Port `{port}` für {minutes} Minuten starten",
        real_serve,
    )


@bot.command(name="sysinfo")
async def sysinfo(ctx: commands.Context):
    """Show host / temperature / memory / disk info (read-only, always allowed for authorized users)."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    # sysinfo is read-only → we still gate on allowed_users but do NOT require LIVE mode
    info = collect_sysinfo()
    await ctx.send(f"**System info**\n```\n{info}\n```")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    log.info("Starting %s (TESTMODE default = %s)", BOT_NAME, TESTMODE)
    bot.run(TOKEN, log_handler=None)  # we already configured logging
