#!/usr/bin/env python3
"""
Preferred Chronos-pi entrypoint.

`python bot.py` and `python start.py` share the same runtime.
Reconnect-safe TESTMODE, placeholder user-id filtering, path ~ expansion,
and run output chunk caps live in bot.py / pi_runtime.py.
"""

from __future__ import annotations

import bot


def main() -> None:
    bot.main()


if __name__ == "__main__":
    main()
