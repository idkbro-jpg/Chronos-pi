import os
import yaml
import discord
from discord.ext import commands
from dotenv import load_dotenv
from typing import Optional

load_dotenv()

# -------------------------------------------------
# Config laden
# -------------------------------------------------
with open("config.yml", "r", encoding="utf-8") as f:
    config = yaml.safe_load(f)

TOKEN = os.getenv("DISCORD_TOKEN")
if not TOKEN:
    raise RuntimeError("DISCORD_TOKEN fehlt in der .env Datei!")

ALLOWED_USERS = set(config.get("allowed_users", []))
BRIDGE_CHANNEL_ID = config["channels"]["bridge"]
MAIN_CHANNEL_ID = config["channels"]["main"]
BRIDGE_MARKER = config.get("bridge_marker", "[CHRONOS]")
BOT_NAME = config["bot"]["name"]
PREFIX = config["bot"].get("prefix", "pi!")

# Globaler Testmodus (wird zur Laufzeit umgeschaltet)
TESTMODE = config.get("testmode_default", True)  # Standard: an, solange kein echter Pi da ist


# -------------------------------------------------
# Bot Setup
# -------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True
intents.guilds = True

bot = commands.Bot(command_prefix=PREFIX, intents=intents)


def is_allowed(user_id: int) -> bool:
    """Prüft ob der User Commands ausführen darf."""
    return user_id in ALLOWED_USERS


async def pi_action(ctx: commands.Context, description: str, real_action=None):
    """
    Zentrale Stelle für alle Raspberry-Pi-spezifischen Aktionen.
    Im TESTMODE wird nur eine Nachricht ausgegeben, nichts wirklich ausgeführt.
    """
    global TESTMODE

    if TESTMODE:
        await ctx.send(f"🧪 **TESTMODE** → Ich würde jetzt **{description}** machen.")
        return False  # Aktion wurde nur simuliert
    else:
        await ctx.send(f"▶️ Führe aus: **{description}**")
        if real_action:
            try:
                result = await real_action() if callable(real_action) else real_action
                if result:
                    await ctx.send(f"✅ Ergebnis:\n```\n{result}\n```")
            except Exception as e:
                await ctx.send(f"❌ Fehler bei der Ausführung:\n```\n{e}\n```")
        return True  # Aktion wurde wirklich ausgeführt


# -------------------------------------------------
# Events
# -------------------------------------------------
@bot.event
async def on_ready():
    global TESTMODE
    mode = "🧪 TESTMODE AN" if TESTMODE else "🚀 LIVE MODE"
    print(f"✅ {BOT_NAME} ist online als {bot.user}")
    print(f"   {mode}")
    print(f"   Bridge-Channel ID: {BRIDGE_CHANNEL_ID}")

    status_text = f"{'TESTMODE' if TESTMODE else 'LIVE'} | the Pi"
    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=status_text
    )
    await bot.change_presence(activity=activity)


@bot.event
async def on_message(message: discord.Message):
    if message.author == bot.user:
        return

    # Bridge-Logik
    if message.channel.id == BRIDGE_CHANNEL_ID:
        content = message.content.strip()

        if content.startswith(BRIDGE_MARKER):
            payload = content[len(BRIDGE_MARKER):].strip().lower()
            print(f"[Bridge] Empfangen: {payload}")

            if payload == "ping":
                await message.channel.send(f"{BRIDGE_MARKER} pong from {BOT_NAME}")
            elif payload == "status":
                mode = "TESTMODE" if TESTMODE else "LIVE"
                await message.channel.send(
                    f"{BRIDGE_MARKER} {BOT_NAME} online | {mode} | latency {round(bot.latency * 1000)}ms"
                )
            elif payload.startswith("do "):
                # Beispiel: [CHRONOS] do wol
                action = payload[3:].strip()
                await message.channel.send(
                    f"{BRIDGE_MARKER} {BOT_NAME} hat Anweisung erhalten: `{action}` "
                    f"(Testmode: {'AN' if TESTMODE else 'AUS'})"
                )

    await bot.process_commands(message)


# -------------------------------------------------
# Basis-Commands
# -------------------------------------------------
@bot.command(name="ping")
async def ping(ctx: commands.Context):
    """Einfacher Ping."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")
    await ctx.send(f"Pong! `{round(bot.latency * 1000)}ms`")


@bot.command(name="status")
async def status(ctx: commands.Context):
    """Zeigt Status der Pi-Instanz inkl. Testmode."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    mode = "🧪 **TESTMODE AN**" if TESTMODE else "🚀 **LIVE MODE**"
    await ctx.send(
        f"**{BOT_NAME}**\n"
        f"• Online als `{bot.user}`\n"
        f"• Latency: `{round(bot.latency * 1000)}ms`\n"
        f"• Modus: {mode}\n"
        f"• Bridge-Channel: `{BRIDGE_CHANNEL_ID}`"
    )


@bot.command(name="whoami")
async def whoami(ctx: commands.Context):
    """Zeigt deine Discord User-ID."""
    await ctx.send(f"Deine Discord User-ID: `{ctx.author.id}`")


@bot.command(name="bridge")
async def bridge_send(ctx: commands.Context, *, text: str):
    """Schickt eine Nachricht in den Bridge-Channel."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    channel = bot.get_channel(BRIDGE_CHANNEL_ID)
    if channel is None:
        return await ctx.send("❌ Bridge-Channel nicht gefunden.")

    await channel.send(f"{BRIDGE_MARKER} {text}")
    await ctx.send(f"✅ In Bridge gesendet: `{text}`")


# -------------------------------------------------
# TESTMODE umschalten
# -------------------------------------------------
@bot.command(name="TESTMODE")
async def toggle_testmode(ctx: commands.Context, state: Optional[str] = None):
    """
    Schaltet den Testmodus um.
    pi!TESTMODE          → Status anzeigen / umschalten
    pi!TESTMODE on       → explizit einschalten
    pi!TESTMODE off      → explizit ausschalten
    """
    global TESTMODE

    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    if state is None:
        # Nur umschalten
        TESTMODE = not TESTMODE
    else:
        state = state.lower()
        if state in ("on", "an", "true", "1"):
            TESTMODE = True
        elif state in ("off", "aus", "false", "0"):
            TESTMODE = False
        else:
            return await ctx.send("Bitte `on` / `off` benutzen (oder gar nichts zum Umschalten).")

    mode_text = "🧪 **TESTMODE ist jetzt AN**\n→ Pi-Aktionen werden nur simuliert." if TESTMODE else \
                "🚀 **LIVE MODE ist jetzt AN**\n→ Pi-Aktionen werden wirklich ausgeführt!"

    await ctx.send(mode_text)

    # Status im Bot-Presence aktualisieren
    status_text = f"{'TESTMODE' if TESTMODE else 'LIVE'} | the Pi"
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching,
        name=status_text
    ))


# -------------------------------------------------
# Beispiel Pi-Funktionen (alle laufen über pi_action)
# -------------------------------------------------
@bot.command(name="wol")
async def wake_on_lan(ctx: commands.Context, mac: str = "00:11:22:33:44:55"):
    """Beispiel: Wake-on-LAN (simuliert im Testmode)."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_wol():
        # Hier würde später der echte WoL-Code stehen
        # z.B. mit wakeonlan oder scapy
        return f"Magic Packet an {mac} gesendet (Platzhalter)"

    await pi_action(ctx, f"Wake-on-LAN an `{mac}` senden", real_wol)


@bot.command(name="serve")
async def temp_server(ctx: commands.Context, folder: str = "/tmp/share", port: int = 8000, minutes: int = 15):
    """Beispiel: Temporären HTTP-Server starten."""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_serve():
        return f"HTTP-Server in `{folder}` auf Port {port} für {minutes} Minuten gestartet (Platzhalter)"

    await pi_action(
        ctx,
        f"temporären HTTP-Server in `{folder}` auf Port `{port}` für {minutes} Minuten starten",
        real_serve
    )


@bot.command(name="run")
async def run_command(ctx: commands.Context, *, cmd: str):
    """
    Beispiel: Beliebigen Befehl auf dem Pi ausführen.
    Im TESTMODE wird er nur angezeigt.
    """
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_run():
        # Später: subprocess oder asyncio.create_subprocess_shell
        return f"Befehl ausgeführt: {cmd} (Platzhalter)"

    await pi_action(ctx, f"folgenden Befehl ausführen: `{cmd}`", real_run)


@bot.command(name="echo")
async def echo_file(ctx: commands.Context, filename: str = "hello.txt", *, text: str = "hello guys"):
    """Beispiel: echo \"text\" > datei"""
    if not is_allowed(ctx.author.id):
        return await ctx.send("⛔ Keine Berechtigung.")

    async def real_echo():
        return f"Datei `{filename}` mit Inhalt geschrieben (Platzhalter)"

    await pi_action(ctx, f"`echo \"{text}\" > {filename}` ausführen", real_echo)


# -------------------------------------------------
# Start
# -------------------------------------------------
if __name__ == "__main__":
    bot.run(TOKEN)
