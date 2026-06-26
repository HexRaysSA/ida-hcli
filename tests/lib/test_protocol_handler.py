"""Tests for the ida:// protocol handler registration hardening."""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from click.testing import CliRunner

from hcli.commands.ida.open import open_ida_link


class TestOpenArgSeparator:
    """The Windows registry command uses `ida open -- "%1"`. These tests confirm the
    `--` separator turns any injected tokens into surplus positionals that click
    rejects, rather than options it would interpret. The handler dispatch is mocked
    so only argument parsing is exercised — no real download/IPC/dialog I/O runs."""

    def test_url_after_separator_is_dispatched_as_the_uri(self):
        # A normal URL after `--` is parsed as the single positional and dispatched
        # verbatim to the handler. A fake handler captures it so no real I/O happens.
        captured: dict[str, str] = {}
        fake = MagicMock()
        fake.matches.return_value = True
        fake.handle.side_effect = lambda uri, *a, **k: captured.__setitem__("uri", uri)

        runner = CliRunner()
        with patch("hcli.commands.ida.open.HANDLERS", [fake]):
            result = runner.invoke(open_ida_link, ["--", "ida://ke/x.i64/functions?url=http://h/a"])

        assert result.exit_code == 0, result.output
        assert captured.get("uri") == "ida://ke/x.i64/functions?url=http://h/a"

    def test_injected_option_after_separator_is_rejected(self):
        # Simulates %1 = `x" --no-launch "` → `ida open -- "x" --no-launch ""`.
        # After `--`, the extra tokens are positionals; open takes only one → error.
        runner = CliRunner()
        result = runner.invoke(open_ida_link, ["--", "ida://ke/x.i64/f", "--no-launch", ""])
        assert result.exit_code != 0
        assert "Got unexpected extra argument" in result.output


class TestWindowsRegistration:
    """Guard the actual registered artifact: the REG_SZ command string must carry the
    `--` separator before %1 (a regression here wouldn't be caught by the click tests)."""

    def test_registered_command_has_separator_before_percent1(self):
        fake_winreg = MagicMock()
        fake_winreg.HKEY_CURRENT_USER = 0
        fake_winreg.REG_SZ = 1
        # `with winreg.CreateKey(...) as key:` — MagicMock supports the context manager
        # protocol out of the box.

        with patch.dict(sys.modules, {"winreg": fake_winreg}):
            from hcli.lib.ida.protocol import setup_windows_protocol_handler

            setup_windows_protocol_handler()

        # SetValueEx(key, name, reserved, type, value) — find the command value.
        commands = [c.args[4] for c in fake_winreg.SetValueEx.call_args_list if "ida open" in str(c.args[4])]
        assert commands, "no ida open command was registered"
        assert commands[0].endswith('ida open -- "%1"'), commands[0]
