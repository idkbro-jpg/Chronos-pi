#!/usr/bin/env python3
"""
Preferred Chronos-pi entrypoint.

Applies process-level safety patches on top of bot.py, then starts the bot.
`python bot.py` still works; this entrypoint fixes reconnect TESTMODE reset
and ignores example placeholder user IDs.
"""

from __future__ import annotations

import discord

import pi_runtime

import bot  # noqa: E402


_testmode_initialized = False


def allowed_user_ids():
    return pi_runtime.allowed_user_ids(
        bot.get_cfg().get("allowed_users"),
        placeholder_uid=bot.PLACEHOLDER_UID,
    )


bot.allowed_user_ids = allowed_user_ids  # type: ignore[assignment]


@bot.bot.event
async def on_ready():
    global _testmode_initialized
    bot.load_config()
    bot.load_mix()
    if pi_runtime.should_init_testmode(_testmode_initialized):
        bot.testmode = bool(bot.get_cfg().get("testmode_default", True))
        _testmode_initialized = True

    allowed = allowed_user_ids()
    if not allowed:
        bot.log.warning(
            "⚠️  allowed_users is empty or still only the example placeholder. "
            "Privileged commands will be denied until you put your real Discord user id."
        )
    for name in ("main", "bridge"):
        cid = bot.channel_id(name)
        if not cid or cid == bot.PLACEHOLDER_CHANNEL or cid == 2222222222222222222:
            bot.log.warning("⚠️  channels.%s looks like a placeholder (%s)", name, cid)

    if bot.execution_mode() == "allowlist" and not bot.allowed_patterns():
        bot.log.warning(
            "⚠️  execution.mode=allowlist but allowed_patterns is empty – "
            "all `run` commands will be blocked."
        )

    activity = discord.Activity(
        type=discord.ActivityType.watching,
        name=str(bot.get_cfg().get("bot", {}).get("status") or "the Pi"),
    )
    await bot.bot.change_presence(activity=activity)
    bot.log.info(
        "Logged in as %s (ID %s) | v%s+patch | TESTMODE=%s | prefix=%s | exec=%s",
        bot.bot.user,
        bot.bot.user.id if bot.bot.user else "?",
        bot.VERSION,
        bot.testmode,
        bot.prefix(),
        bot.execution_mode(),
    )
    bot.log_event(
        "startup",
        detail=f"v{bot.VERSION}+patch testmode={bot.testmode} exec={bot.execution_mode()}",
    )


def main() -> None:
    bot.main()


if __name__ == "__main__":
    main()
