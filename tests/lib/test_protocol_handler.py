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


class TestPosixHandlerArtifacts:
    """The macOS AppleScript and Linux desktop Exec= are the real registered artifacts;
    assert each carries the `--` option terminator before the URL placeholder."""

    def test_macos_applescript_has_separator(self):
        from hcli.lib.ida.protocol import _macos_handler_applescript

        script = _macos_handler_applescript(
            "/path/hcli", "/home/u/Library/Logs/idb_handler.log", "/home/u/Library/Logs"
        )
        assert "ida open -- " in script
        # and it ensures the log dir exists at click time so the redirect can't break
        assert "mkdir -p" in script

    def test_linux_desktop_exec_has_separator(self):
        from hcli.lib.ida.protocol import _linux_desktop_entry

        entry = _linux_desktop_entry("/path/hcli")
        exec_line = next(ln for ln in entry.splitlines() if ln.startswith("Exec="))
        assert exec_line.endswith("ida open -- %u"), exec_line

    def test_linux_registration_writes_separator_to_desktop_file(self, tmp_path, monkeypatch):
        # Real-artifact check: the written .desktop file must carry `-- %u`.
        monkeypatch.setattr("hcli.lib.ida.protocol.Path.home", lambda: tmp_path)
        monkeypatch.setattr("hcli.lib.ida.protocol.get_hcli_command", lambda: ["/path/hcli"])
        monkeypatch.setattr("hcli.lib.ida.protocol.subprocess.run", lambda *a, **k: MagicMock())

        from hcli.lib.ida.protocol import setup_linux_protocol_handler

        setup_linux_protocol_handler()

        desktop = tmp_path / ".local" / "share" / "applications" / "hcli-idb-handler.desktop"
        exec_line = next(ln for ln in desktop.read_text().splitlines() if ln.startswith("Exec="))
        assert exec_line.endswith("ida open -- %u"), exec_line
