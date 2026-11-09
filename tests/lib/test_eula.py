import io
from pathlib import Path

import pytest
from click.testing import CliRunner
from rich.console import Console
from rich.pager import Pager

from hcli.commands.ida.accept_eula import accept_eula_command
from hcli.commands.ida.install import handle_eula_for_install, install
from hcli.lib.ida import get_ida_binary_path, get_ida_path, is_idalib_capable_installation
from hcli.lib.ida.eula import (
    EulaAction,
    EulaNeedsTerminalError,
    ask_eula_acceptance,
    get_eula_action,
    get_eula_consent,
    get_license_text_path,
    render_eula_record,
)
from hcli.lib.util.io import get_os

EULA_TEXT = "Hex-Rays End User License Agreement\n\n1. GRANT AND SCOPE OF LICENSE\n"


class RecordingPager(Pager):
    def __init__(self) -> None:
        self.pages: list[str] = []

    def show(self, content: str) -> None:
        self.pages.append(content)


def make_console() -> Console:
    return Console(file=io.StringIO(), width=80, force_terminal=False)


def get_output(console: Console) -> str:
    assert isinstance(console.file, io.StringIO)
    return console.file.getvalue()


def make_fake_ida_dir(root: Path, with_idalib: bool = False, with_license_text: bool = True) -> Path:
    ida_dir = root / ("ida.app" if get_os() == "mac" else "ida")
    binary = get_ida_binary_path(ida_dir)
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    if with_license_text:
        (get_ida_path(ida_dir) / "license.txt").write_text(EULA_TEXT, encoding="utf-8")
    if with_idalib:
        filename = {"windows": "idalib.dll", "linux": "libidalib.so", "mac": "libidalib.dylib"}[get_os()]
        (get_ida_path(ida_dir) / filename).write_bytes(b"")
        assert is_idalib_capable_installation(ida_dir)
    return ida_dir


@pytest.mark.parametrize(
    "accept_eula, interactive, expected",
    [
        (True, True, EulaAction.ACCEPT),
        (True, False, EulaAction.ACCEPT),
        (False, True, EulaAction.SKIP),
        (False, False, EulaAction.SKIP),
        (None, True, EulaAction.PROMPT),
        (None, False, EulaAction.NO_TERMINAL),
    ],
)
def test_get_eula_action(accept_eula, interactive, expected):
    assert get_eula_action(accept_eula, interactive) is expected


def test_license_text_is_next_to_the_ida_binary(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path)
    assert get_license_text_path(ida_dir) == get_ida_path(ida_dir) / "license.txt"
    assert get_license_text_path(ida_dir).read_text(encoding="utf-8") == EULA_TEXT


def test_ask_eula_acceptance_pages_the_text_then_asks(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path)
    console = make_console()
    pager = RecordingPager()

    accepted = ask_eula_acceptance(get_license_text_path(ida_dir), console, pager=pager, stream=io.StringIO("y\n"))

    assert accepted
    assert len(pager.pages) == 1
    assert "You must accept the terms" in pager.pages[0]
    assert "1. GRANT AND SCOPE OF LICENSE" in pager.pages[0]
    assert "Do you accept the Hex-Rays EULA?" in get_output(console)


@pytest.mark.parametrize("answer", ["n\n", "\n", ""])
def test_ask_eula_acceptance_defaults_to_no(tmp_path, answer):
    ida_dir = make_fake_ida_dir(tmp_path)
    console = make_console()

    accepted = ask_eula_acceptance(
        get_license_text_path(ida_dir), console, pager=RecordingPager(), stream=io.StringIO(answer)
    )

    assert not accepted


def test_ask_eula_acceptance_without_license_text_fails(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path, with_license_text=False)

    with pytest.raises(FileNotFoundError):
        ask_eula_acceptance(
            get_license_text_path(ida_dir), make_console(), pager=RecordingPager(), stream=io.StringIO("y\n")
        )


def test_get_eula_consent_accept_does_not_page_or_ask(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path, with_license_text=False)
    console = make_console()
    pager = RecordingPager()

    assert get_eula_consent(get_license_text_path(ida_dir), EulaAction.ACCEPT, console, pager=pager)
    assert pager.pages == []
    assert get_output(console) == ""


def test_get_eula_consent_skip(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path)
    assert not get_eula_consent(get_license_text_path(ida_dir), EulaAction.SKIP, make_console())


def test_get_eula_consent_no_terminal(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path)
    license_path = get_license_text_path(ida_dir)

    with pytest.raises(EulaNeedsTerminalError) as excinfo:
        get_eula_consent(license_path, EulaAction.NO_TERMINAL, make_console())

    assert excinfo.value.license_path == license_path


def test_get_eula_consent_prompt(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path)
    pager = RecordingPager()

    assert get_eula_consent(
        get_license_text_path(ida_dir), EulaAction.PROMPT, make_console(), pager=pager, stream=io.StringIO("Y\n")
    )
    assert len(pager.pages) == 1


def test_render_eula_record(tmp_path):
    path = tmp_path / "license.txt"
    assert render_eula_record(path) == f"Accepted the Hex-Rays EULA ({path})"


def test_accept_eula_command_missing_path_fails(tmp_path):
    result = CliRunner().invoke(accept_eula_command, [str(tmp_path / "missing")])

    assert result.exit_code == 1
    assert "Path does not exist" in result.output


def test_accept_eula_command_non_ida_dir_fails(tmp_path):
    result = CliRunner().invoke(accept_eula_command, [str(tmp_path)])

    assert result.exit_code == 1
    assert "Not a valid IDA installation directory" in result.output


def test_accept_eula_command_without_idalib_fails(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path, with_idalib=False)

    result = CliRunner().invoke(accept_eula_command, [str(ida_dir), "--yes"])

    assert result.exit_code == 1
    assert "idalib not available" in result.output


def test_accept_eula_command_without_terminal_fails(tmp_path):
    ida_dir = make_fake_ida_dir(tmp_path, with_idalib=True)

    result = CliRunner(mix_stderr=False).invoke(accept_eula_command, [str(ida_dir)])

    assert result.exit_code == 1
    assert "EULA acceptance needs a terminal" in result.output
    assert "--yes" in result.output
    assert "Accepted the Hex-Rays EULA" not in result.stderr


def make_installer(tmp_path: Path) -> Path:
    installer = tmp_path / "ida-pro_95_x64linux.run"
    installer.write_bytes(b"")
    return installer


@pytest.mark.parametrize(
    "flags, expected",
    [
        ([], "Ask to accept EULA (only if the installation includes idalib)"),
        (["--accept-eula"], "Accept EULA (--accept-eula, only if the installation includes idalib)"),
        (["-a"], "Accept EULA (--accept-eula, only if the installation includes idalib)"),
        (["--yes"], "Ask to accept EULA (only if the installation includes idalib)"),
    ],
)
def test_install_dry_run_lists_eula_action(tmp_path, flags, expected):
    installer = make_installer(tmp_path)

    result = CliRunner().invoke(
        install, [str(installer), "--install-dir", str(tmp_path / "ida"), "--dry-run", "--no-set-default", *flags]
    )

    assert result.exit_code == 0, result.output
    assert expected in result.output


@pytest.mark.parametrize("flag", ["--no-accept-eula", "-A"])
def test_install_dry_run_without_eula(tmp_path, flag):
    installer = make_installer(tmp_path)

    result = CliRunner().invoke(install, [str(installer), "--install-dir", str(tmp_path / "ida"), "--dry-run", flag])

    assert result.exit_code == 0, result.output
    assert "EULA" not in result.output


def test_install_without_terminal_skips_eula_with_a_hint(tmp_path, capsys):
    ida_dir = make_fake_ida_dir(tmp_path, with_idalib=True)

    handle_eula_for_install(ida_dir, EulaAction.NO_TERMINAL)

    err = capsys.readouterr().err
    assert "Skipped EULA acceptance" in err
    assert str(get_license_text_path(ida_dir)) in err
    assert f'ida accept-eula --yes "{ida_dir}"' in err
    assert "Accepted the Hex-Rays EULA" not in err


def test_install_prompt_without_license_text_skips_eula_with_a_hint(tmp_path, capsys):
    ida_dir = make_fake_ida_dir(tmp_path, with_idalib=True, with_license_text=False)

    handle_eula_for_install(ida_dir, EulaAction.PROMPT)

    err = capsys.readouterr().err
    assert "EULA text not found" in err
    assert f'ida accept-eula --yes "{ida_dir}"' in err
