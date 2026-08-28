#!/usr/bin/env python3
"""
Preferred Chronos-pi entrypoint.

`python bot.py` and `python start.py` now share the same runtime:
reconnect-safe TESTMODE and placeholder user-id filtering live in bot.py.
This file stays as the documented / systemd entrypoint.
"""

from __future__ import annotations

import bot


def main() -> None:
    bot.main()


if __name__ == "__main__":
    main()
