#!/usr/bin/env python3
"""
Preferred Chronos-pi entrypoint.

Safety (reconnect-stable TESTMODE, placeholder IDs, run chunk cap, aliases)
lives in bot.py, so `python start.py` and `python bot.py` behave the same.
"""

from __future__ import annotations

import bot


def main() -> None:
    bot.main()


if __name__ == "__main__":
    main()
