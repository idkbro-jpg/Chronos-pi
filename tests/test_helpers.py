#!/usr/bin/env python3
"""Lightweight checks for Chronos-pi helpers (no Discord login)."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import bot  # noqa: E402
import pi_runtime  # noqa: E402


def test_normalize_mac():
    assert bot.normalize_mac("aa-bb-cc-dd-ee-ff") == "AA:BB:CC:DD:EE:FF"
    assert bot.normalize_mac("not-a-mac") is None
    assert bot.normalize_mac("") is None


def test_command_policy_unrestricted_default():
    bot._cfg = bot._defaults()
    ok, reason = bot.command_allowed_by_policy("rm -rf /")
    assert ok and reason == ""


def test_command_policy_allowlist_exact_and_glob_and_re():
    cfg = bot._defaults()
    cfg["execution"] = {
        "mode": "allowlist",
        "allowed_patterns": ["uptime", "df -h*", "re:^vcgencmd\\b"],
    }
    bot._cfg = cfg
    assert bot.command_allowed_by_policy("uptime")[0] is True
    assert bot.command_allowed_by_policy("uptime; rm")[0] is False
    assert bot.command_allowed_by_policy("df -h /")[0] is True
    assert bot.command_allowed_by_policy("vcgencmd measure_temp")[0] is True
    assert bot.command_allowed_by_policy("echo vcgencmd")[0] is False


def test_placeholder_ids_stripped_via_runtime():
    ids = pi_runtime.allowed_user_ids([bot.PLACEHOLDER_UID, "0", 42], bot.PLACEHOLDER_UID)
    assert ids == {42}


def test_bot_allowed_users_strips_placeholder():
    cfg = bot._defaults()
    cfg["allowed_users"] = [bot.PLACEHOLDER_UID, 42]
    bot._cfg = cfg
    assert bot.allowed_user_ids() == {42}
    assert bot.is_allowed(bot.PLACEHOLDER_UID) is False
    assert bot.is_allowed(42) is True


def test_run_max_chunks_cap():
    bot._cfg = bot._defaults()
    assert bot.run_max_chunks() == 4
    bot._cfg["limits"]["run_max_chunks"] = 99
    assert bot.run_max_chunks() == 10


def test_tilde_expand_before_allowlist():
    home_file = Path.home() / "chronos-pi-should-not-pass-unless-home-is-allowed"
    raw = Path("~/chronos-pi-should-not-pass-unless-home-is-allowed")
    allowed = bot._is_path_allowed(raw)
    expected = bot._is_path_allowed(home_file)
    assert allowed == expected


def test_path_allowed_tmp_and_cwd_not_root():
    tmp_file = Path("/tmp/chronos-pi-test-file")
    assert bot._is_path_allowed(tmp_file) is True
    assert bot._is_path_allowed(Path("/etc/passwd")) is False
    assert bot._is_path_allowed(Path.cwd() / "bot.py") is True


def test_deep_merge():
    out = bot._deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"c": 9}, "d": 3})
    assert out == {"a": {"b": 1, "c": 9}, "d": 3}


def test_chunk_and_safe_code():
    assert bot._chunk("abcdef", 2) == ["ab", "cd", "ef"]
    assert "```" not in bot._safe_code("```rm```") or "\u200b" in bot._safe_code("```")


def test_pi_runtime_placeholder_and_reconnect_flag():
    assert pi_runtime.allowed_user_ids([pi_runtime.PLACEHOLDER_UID, 7]) == {7}
    assert pi_runtime.should_init_testmode(False) is True
    assert pi_runtime.should_init_testmode(True) is False
    assert pi_runtime.run_max_chunks({"run_max_chunks": 99}) == 10
    assert pi_runtime.mix_aliases({"aliases": {"a": {}}}) == {"a": {}}


def test_mix_aliases_loaded():
    aliases = pi_runtime.mix_aliases({"aliases": {"ping-all": {"action": "ping", "targets": ["pi"]}}})
    assert "ping-all" in aliases


if __name__ == "__main__":
    tests = [v for k, v in list(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"PASS {fn.__name__}")
        except Exception as e:
            failed += 1
            print(f"FAIL {fn.__name__}: {e}")
    if failed:
        sys.exit(1)
    print(f"OK {len(tests)} tests")
