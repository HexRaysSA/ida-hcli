"""Tests for platform-specific environment variable configuration.

Tests the decision tree (build_configuration_plan) and file writing
(execute_configuration_plan) without touching the real system.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from hcli.lib.ida.python.platform_env import (
    ENVIRONMENT_D_FILENAME,
    ConfigurationStep,
    _build_linux_plan,
    _build_macos_plan,
    _build_windows_plan,
    _file_already_has_content,
    build_configuration_plan,
    detect_login_shell,
    execute_step,
    get_login_profile_path,
    render_shell_export,
)

NAME = "IDAPYTHON_VENV_EXECUTABLE"
VALUE = "/home/user/.idapro/venv/bin/python"
WIN_VALUE = r"C:\Users\user\.idapro\venv\Scripts\python.exe"


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------


def test_detect_login_shell():
    with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
        assert detect_login_shell() == "zsh"
    with patch.dict("os.environ", {"SHELL": "/usr/bin/fish"}):
        assert detect_login_shell() == "fish"
    with patch.dict("os.environ", {"SHELL": "/bin/bash"}):
        assert detect_login_shell() == "bash"
    with patch.dict("os.environ", {"SHELL": ""}):
        assert detect_login_shell() == "unknown"
    with patch.dict("os.environ", {"SHELL": "/bin/nu"}):
        assert detect_login_shell() == "unknown"


def test_get_login_profile_path():
    home = Path("/home/user")
    assert get_login_profile_path("zsh", home) == home / ".zprofile"
    assert get_login_profile_path("bash", home) == home / ".bash_profile"
    assert get_login_profile_path("fish", home) == home / ".config" / "fish" / "config.fish"
    assert get_login_profile_path("sh", home) == home / ".profile"
    assert get_login_profile_path("unknown", home) is None


def test_render_shell_export():
    assert render_shell_export("X", "/v/bin/python", "zsh") == 'export X="/v/bin/python"'
    assert render_shell_export("X", "/v/bin/python", "bash") == 'export X="/v/bin/python"'
    assert render_shell_export("X", "/v/bin/python", "fish") == 'set -gx X "/v/bin/python"'
    assert render_shell_export("X", "/v/bin/python", "sh") == 'export X="/v/bin/python"'


# ---------------------------------------------------------------------------
# Windows plan
# ---------------------------------------------------------------------------


def test_windows_plan_uses_powershell():
    plan = _build_windows_plan(NAME, WIN_VALUE)
    assert len(plan.steps) == 1
    step = plan.steps[0]
    assert step.kind == "windows-user-env"
    assert step.command is not None
    assert step.command[0] == "powershell"
    assert "SetEnvironmentVariable" in step.command[-1]
    assert NAME in step.command[-1]
    assert WIN_VALUE in step.command[-1]
    assert '"User"' in step.command[-1]


def test_windows_plan_manual_instructions_mention_powershell_and_gui():
    plan = _build_windows_plan(NAME, WIN_VALUE)
    assert "PowerShell" in plan.manual_instructions
    assert "Edit environment variables" in plan.manual_instructions


# ---------------------------------------------------------------------------
# macOS plan
# ---------------------------------------------------------------------------


def test_macos_plan_has_launchagent_launchctl_and_profile():
    home = Path("/Users/testuser")
    with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
        plan = _build_macos_plan(NAME, VALUE, home)

    kinds = [s.kind for s in plan.steps]
    assert "macos-launchagent" in kinds
    assert "macos-launchctl-setenv" in kinds
    assert "shell-profile" in kinds

    agent_step = next(s for s in plan.steps if s.kind == "macos-launchagent")
    assert agent_step.file_path == home / "Library" / "LaunchAgents" / "com.hex-rays.idapython-venv.plist"
    assert agent_step.file_content is not None
    assert "launchctl" in agent_step.file_content
    assert VALUE in agent_step.file_content

    profile_step = next(s for s in plan.steps if s.kind == "shell-profile")
    assert profile_step.file_path == home / ".zprofile"


def test_macos_plan_needs_logout():
    home = Path("/Users/testuser")
    with patch.dict("os.environ", {"SHELL": "/bin/zsh"}):
        plan = _build_macos_plan(NAME, VALUE, home)
    assert plan.needs_logout


# ---------------------------------------------------------------------------
# Linux plan
# ---------------------------------------------------------------------------


def test_linux_plan_with_systemd_has_environment_d_and_profile():
    home = Path("/home/testuser")
    with (
        patch.dict("os.environ", {"SHELL": "/bin/bash"}),
        patch("hcli.lib.ida.python.platform_env.has_systemd_user", return_value=True),
    ):
        plan = _build_linux_plan(NAME, VALUE, home)

    kinds = [s.kind for s in plan.steps]
    assert "linux-environment-d" in kinds
    assert "shell-profile" in kinds

    env_d_step = next(s for s in plan.steps if s.kind == "linux-environment-d")
    assert env_d_step.file_path is not None
    assert ENVIRONMENT_D_FILENAME in env_d_step.file_path.name
    assert env_d_step.file_content == f"{NAME}={VALUE}"

    profile_step = next(s for s in plan.steps if s.kind == "shell-profile")
    assert profile_step.file_path == home / ".bash_profile"


def test_linux_plan_without_systemd_has_only_profile():
    home = Path("/home/testuser")
    with (
        patch.dict("os.environ", {"SHELL": "/bin/zsh"}),
        patch("hcli.lib.ida.python.platform_env.has_systemd_user", return_value=False),
    ):
        plan = _build_linux_plan(NAME, VALUE, home)

    kinds = [s.kind for s in plan.steps]
    assert "linux-environment-d" not in kinds
    assert "shell-profile" in kinds

    profile_step = next(s for s in plan.steps if s.kind == "shell-profile")
    assert profile_step.file_path == home / ".zprofile"


def test_linux_plan_with_unknown_shell():
    home = Path("/home/testuser")
    with (
        patch.dict("os.environ", {"SHELL": "/bin/nu"}),
        patch("hcli.lib.ida.python.platform_env.has_systemd_user", return_value=True),
    ):
        plan = _build_linux_plan(NAME, VALUE, home)

    kinds = [s.kind for s in plan.steps]
    assert "linux-environment-d" in kinds
    assert "shell-profile" not in kinds
    assert "~/.profile" in plan.manual_instructions


# ---------------------------------------------------------------------------
# build_configuration_plan dispatch
# ---------------------------------------------------------------------------


def test_build_configuration_plan_dispatches_to_windows():
    with patch("hcli.lib.ida.python.platform_env.is_windows", return_value=True):
        plan = build_configuration_plan(NAME, WIN_VALUE)
    assert plan.steps[0].kind == "windows-user-env"


def test_build_configuration_plan_dispatches_to_macos():
    with (
        patch("hcli.lib.ida.python.platform_env.is_windows", return_value=False),
        patch("hcli.lib.ida.python.platform_env.is_macos", return_value=True),
        patch.dict("os.environ", {"SHELL": "/bin/zsh"}),
    ):
        plan = build_configuration_plan(NAME, VALUE, home=Path("/Users/test"))
    kinds = [s.kind for s in plan.steps]
    assert "macos-launchagent" in kinds


def test_build_configuration_plan_dispatches_to_linux():
    with (
        patch("hcli.lib.ida.python.platform_env.is_windows", return_value=False),
        patch("hcli.lib.ida.python.platform_env.is_macos", return_value=False),
        patch("hcli.lib.ida.python.platform_env.has_systemd_user", return_value=True),
        patch.dict("os.environ", {"SHELL": "/bin/bash"}),
    ):
        plan = build_configuration_plan(NAME, VALUE, home=Path("/home/test"))
    kinds = [s.kind for s in plan.steps]
    assert "linux-environment-d" in kinds


# ---------------------------------------------------------------------------
# Execution: file writing
# ---------------------------------------------------------------------------


def test_file_already_has_content(tmp_path: Path):
    f = tmp_path / "test.conf"
    f.write_text("IDAPYTHON_VENV_EXECUTABLE=/some/path\n")
    assert _file_already_has_content(f, "IDAPYTHON_VENV_EXECUTABLE=/some/path")
    assert not _file_already_has_content(f, "IDAPYTHON_VENV_EXECUTABLE=/other/path")
    assert not _file_already_has_content(tmp_path / "nonexistent", "anything")


def test_execute_step_creates_environment_d(tmp_path: Path):
    step = ConfigurationStep(
        kind="linux-environment-d",
        description="Create environment.d conf",
        file_path=tmp_path / ".config" / "environment.d" / ENVIRONMENT_D_FILENAME,
        file_content=f"{NAME}={VALUE}",
        command=None,
        needs_logout=True,
    )
    result = execute_step(step)
    assert result.success
    assert not result.skipped
    assert step.file_path is not None
    assert step.file_path.read_text() == f"{NAME}={VALUE}"


def test_execute_step_skips_when_already_present(tmp_path: Path):
    conf = tmp_path / ENVIRONMENT_D_FILENAME
    conf.write_text(f"{NAME}={VALUE}\n")
    step = ConfigurationStep(
        kind="linux-environment-d",
        description="Create environment.d conf",
        file_path=conf,
        file_content=f"{NAME}={VALUE}",
        command=None,
        needs_logout=True,
    )
    result = execute_step(step)
    assert result.success
    assert result.skipped


def test_execute_step_appends_to_shell_profile(tmp_path: Path):
    profile = tmp_path / ".zprofile"
    profile.write_text("# existing content\n")
    line = f'export {NAME}="{VALUE}"'
    step = ConfigurationStep(
        kind="shell-profile",
        description=f"Add export to {profile}",
        file_path=profile,
        file_content=line,
        command=None,
        needs_logout=False,
    )
    result = execute_step(step)
    assert result.success
    assert not result.skipped
    assert line in profile.read_text()


def test_execute_step_profile_idempotent(tmp_path: Path):
    profile = tmp_path / ".zprofile"
    line = f'export {NAME}="{VALUE}"'
    profile.write_text(line + "\n")
    step = ConfigurationStep(
        kind="shell-profile",
        description=f"Add export to {profile}",
        file_path=profile,
        file_content=line,
        command=None,
        needs_logout=False,
    )
    result = execute_step(step)
    assert result.success
    assert result.skipped


def test_execute_step_creates_launchagent_plist(tmp_path: Path):
    plist_path = tmp_path / "Library" / "LaunchAgents" / "com.hex-rays.idapython-venv.plist"
    plist_content = "<plist>test</plist>"
    step = ConfigurationStep(
        kind="macos-launchagent",
        description="Create LaunchAgent",
        file_path=plist_path,
        file_content=plist_content,
        command=None,
        needs_logout=True,
    )
    result = execute_step(step)
    assert result.success
    assert plist_path.read_text() == plist_content


def test_execute_step_updates_existing_launchagent(tmp_path: Path):
    plist_path = tmp_path / "com.hex-rays.idapython-venv.plist"
    plist_path.write_text("<plist>old</plist>")
    step = ConfigurationStep(
        kind="macos-launchagent",
        description="Create LaunchAgent",
        file_path=plist_path,
        file_content="<plist>new</plist>",
        command=None,
        needs_logout=True,
    )
    result = execute_step(step)
    assert result.success
    assert not result.skipped
    assert plist_path.read_text() == "<plist>new</plist>"
