#!/usr/bin/env python3
"""
Preferred Chronos-pi entrypoint.

`python start.py` and `python bot.py` now share the same runtime.
Reconnect-safe TESTMODE, placeholder stripping and chunk caps live in bot.py.
"""

from __future__ import annotations

import bot


def main() -> None:
    bot.main()


if __name__ == "__main__":
    main()
