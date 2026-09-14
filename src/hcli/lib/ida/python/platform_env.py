"""Persist environment variables across platforms for IDA's Python environment.

HCLI sets IDAPYTHON_VENV_EXECUTABLE so that IDA loads the correct virtual
environment regardless of how it starts: terminal, Dock, Start Menu, or
desktop file.  The mechanism differs by OS and session type.

Three layers:

  1. **Platform detection** — pure predicates (OS, shell, systemd, session).
  2. **Plan** — `build_configuration_plan` returns steps, warnings, and
     manual instructions without side effects.
  3. **Execute** — `execute_configuration_plan` carries out the plan.
"""

from __future__ import annotations

import logging
import os
import platform
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform detection — one predicate per question
# ---------------------------------------------------------------------------


def is_windows() -> bool:
    return platform.system() == "Windows"


def is_macos() -> bool:
    return platform.system() == "Darwin"


def is_linux() -> bool:
    return platform.system() == "Linux"


SessionType = Literal["wayland", "x11", "tty", "unknown"]


def detect_session_type() -> SessionType:
    if os.environ.get("WAYLAND_DISPLAY"):
        return "wayland"
    if os.environ.get("DISPLAY"):
        return "x11"
    if os.environ.get("TERM"):
        return "tty"
    return "unknown"


def has_systemd_user() -> bool:
    if not is_linux():
        return False
    return Path("/run/systemd/system").is_dir()


ShellKind = Literal["bash", "zsh", "fish", "sh", "unknown"]


def detect_login_shell() -> ShellKind:
    shell = os.environ.get("SHELL", "")
    if not shell:
        return "unknown"
    name = Path(shell).name
    if name in ("bash", "zsh", "fish", "sh"):
        return name  # type: ignore[return-value]
    return "unknown"


def get_login_profile_path(shell: ShellKind, home: Path) -> Path | None:
    if shell == "zsh":
        return home / ".zprofile"
    if shell == "bash":
        return home / ".bash_profile"
    if shell == "fish":
        return home / ".config" / "fish" / "config.fish"
    if shell == "sh":
        return home / ".profile"
    return None


def render_shell_export(name: str, value: str, shell: ShellKind) -> str:
    if shell == "fish":
        return f'set -gx {name} "{value}"'
    return f'export {name}="{value}"'


# ---------------------------------------------------------------------------
# Configuration plan — what HCLI will do, before doing it
# ---------------------------------------------------------------------------

StepKind = Literal[
    "windows-user-env",
    "macos-launchagent",
    "macos-launchctl-setenv",
    "linux-environment-d",
    "shell-profile",
]


@dataclass(frozen=True)
class ConfigurationStep:
    kind: StepKind
    description: str
    file_path: Path | None
    file_content: str | None
    command: list[str] | None
    needs_logout: bool


@dataclass(frozen=True)
class ConfigurationPlan:
    steps: list[ConfigurationStep]
    warnings: list[str]
    env_var_name: str
    env_var_value: str
    manual_instructions: str

    @property
    def needs_logout(self) -> bool:
        return any(step.needs_logout for step in self.steps)


LAUNCHAGENT_TEMPLATE = """\
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.hex-rays.idapython-venv-executable</string>
    <key>ProgramArguments</key>
    <array>
        <string>/bin/launchctl</string>
        <string>setenv</string>
        <string>{name}</string>
        <string>{value}</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
</dict>
</plist>
"""

ENVIRONMENT_D_FILENAME = "50-hexrays-idapython-venv-executable.conf"


def _build_windows_plan(name: str, value: str) -> ConfigurationPlan:
    ps_command = f'[Environment]::SetEnvironmentVariable("{name}", "{value}", "User")'
    step = ConfigurationStep(
        kind="windows-user-env",
        description=f"Set {name} as a user environment variable via PowerShell",
        file_path=None,
        file_content=None,
        command=["powershell", "-NoProfile", "-Command", ps_command],
        needs_logout=False,
    )
    manual = (
        f"Set {name} for your user account using one of these methods:\n"
        f"\n"
        f"  PowerShell:\n"
        f"    {ps_command}\n"
        f"\n"
        f"  GUI:\n"
        f'    Start > "Edit environment variables for your account" > New\n'
        f"    Name:  {name}\n"
        f"    Value: {value}\n"
        f"\n"
        f"Restart IDA and any open terminals for the change to take effect."
    )
    return ConfigurationPlan(
        steps=[step],
        warnings=[],
        env_var_name=name,
        env_var_value=value,
        manual_instructions=manual,
    )


def _build_macos_plan(name: str, value: str, home: Path) -> ConfigurationPlan:
    steps: list[ConfigurationStep] = []
    warnings: list[str] = []
    shell = detect_login_shell()
    profile = get_login_profile_path(shell, home)

    plist_path = home / "Library" / "LaunchAgents" / "com.hex-rays.idapython-venv-executable.plist"
    plist_content = LAUNCHAGENT_TEMPLATE.format(name=name, value=value)

    steps.extend(
        [
            ConfigurationStep(
                kind="macos-launchagent",
                description=f"Create LaunchAgent so IDA launched from Finder/Dock inherits {name}",
                file_path=plist_path,
                file_content=plist_content,
                command=None,
                needs_logout=True,
            ),
            ConfigurationStep(
                kind="macos-launchctl-setenv",
                description=f"Apply {name} to the current session (immediate, no logout needed)",
                file_path=None,
                file_content=None,
                command=["launchctl", "setenv", name, value],
                needs_logout=False,
            ),
        ]
    )

    if profile is not None:
        line = render_shell_export(name, value, shell)
        steps.append(
            ConfigurationStep(
                kind="shell-profile",
                description=f"Add export to {profile} for terminal sessions",
                file_path=profile,
                file_content=line,
                command=None,
                needs_logout=False,
            )
        )
    else:
        shell_name = os.environ.get("SHELL", "your shell")
        warnings.append(
            f"HCLI does not know how to configure {shell_name}. "
            f"IDA launched from Finder/Dock will work (via the LaunchAgent), but "
            f"terminal sessions will not see {name} until you add the export to "
            f"your shell's login profile manually."
        )

    manual_parts = [
        f"Configure {name} for your system:\n",
        (
            f"  For Finder/Dock (LaunchAgent):\n"
            f"    Create {plist_path} with the contents shown above,\n"
            f"    then run: launchctl setenv {name} {value}\n"
        ),
    ]
    if profile is not None:
        line = render_shell_export(name, value, shell)
        manual_parts.append(f"  For terminal sessions:\n    Add to {profile}:\n      {line}\n")
    else:
        export = render_shell_export(name, value, "sh")
        manual_parts.append(f"  For terminal sessions:\n    Add to your shell's login profile:\n      {export}\n")
    manual_parts.append(
        "Log out and back in for the LaunchAgent to take effect.\n"
        "New terminal windows pick up the shell profile change immediately."
    )
    return ConfigurationPlan(
        steps=steps,
        warnings=warnings,
        env_var_name=name,
        env_var_value=value,
        manual_instructions="\n".join(manual_parts),
    )


def _build_linux_plan(name: str, value: str, home: Path) -> ConfigurationPlan:
    steps: list[ConfigurationStep] = []
    warnings: list[str] = []
    shell = detect_login_shell()
    profile = get_login_profile_path(shell, home)
    systemd = has_systemd_user()
    session = detect_session_type()

    if systemd:
        env_d_path = home / ".config" / "environment.d" / ENVIRONMENT_D_FILENAME
        steps.append(
            ConfigurationStep(
                kind="linux-environment-d",
                description=f"Create {env_d_path} for graphical desktop sessions",
                file_path=env_d_path,
                file_content=f"{name}={value}",
                command=None,
                needs_logout=True,
            )
        )

    if profile is not None:
        line = render_shell_export(name, value, shell)
        steps.append(
            ConfigurationStep(
                kind="shell-profile",
                description=f"Add export to {profile} for terminal/SSH sessions",
                file_path=profile,
                file_content=line,
                command=None,
                needs_logout=False,
            )
        )

    if not systemd and profile is not None:
        if session == "wayland":
            warnings.append(
                "This system does not use systemd, so environment.d is not available. "
                "Under Wayland, there is no reliable mechanism to set per-user environment "
                "variables for graphical apps. The shell profile may not reach IDA launched "
                "from the desktop. If IDA does not see the variable, configure it in your "
                "Wayland compositor's environment settings (e.g., sway: `exec`, "
                "Hyprland: `env =`, labwc: environment config)."
            )
        else:
            warnings.append(
                "This system does not use systemd, so environment.d is not available. "
                "Whether the shell profile reaches graphical sessions depends on your "
                "display manager. If IDA launched from the desktop does not see the "
                "variable, add it to ~/.xprofile (for X11) or configure it in your "
                "desktop environment's session settings."
            )
    elif not systemd and profile is None:
        warnings.append(
            "This system does not use systemd, and HCLI could not detect your shell. "
            f"HCLI cannot automatically configure {name}. "
            "Set it in your shell's login profile and in your desktop environment's "
            "session configuration (e.g., ~/.xprofile for X11, or your Wayland "
            "compositor's environment settings)."
        )
    elif systemd and profile is None:
        shell_name = os.environ.get("SHELL", "your shell")
        warnings.append(
            f"HCLI does not know how to configure {shell_name}. "
            f"IDA launched from the desktop will work (via environment.d), but "
            f"terminal and SSH sessions will not see {name} until you add the export "
            f"to your shell's login profile manually."
        )

    manual_parts = [f"Configure {name} for your system:\n"]
    if systemd:
        env_d_path = home / ".config" / "environment.d" / ENVIRONMENT_D_FILENAME
        manual_parts.append(
            f"  For graphical sessions (GNOME, KDE, etc.):\n    Create {env_d_path} with:\n      {name}={value}\n"
        )
    if profile is not None:
        line = render_shell_export(name, value, shell)
        manual_parts.append(f"  For terminal/SSH sessions:\n    Add to {profile}:\n      {line}\n")
    else:
        export = render_shell_export(name, value, "sh")
        manual_parts.append(
            f"  For terminal/SSH sessions:\n    Add to your shell's login profile (e.g. ~/.profile):\n      {export}\n"
        )
    manual_parts.append("Log out and back in for the changes to take effect.")
    return ConfigurationPlan(
        steps=steps,
        warnings=warnings,
        env_var_name=name,
        env_var_value=value,
        manual_instructions="\n".join(manual_parts),
    )


def build_configuration_plan(
    name: str,
    value: str,
    *,
    home: Path | None = None,
) -> ConfigurationPlan:
    if home is None:
        home = Path.home()

    if is_windows():
        return _build_windows_plan(name, value)
    if is_macos():
        return _build_macos_plan(name, value, home)
    return _build_linux_plan(name, value, home)


# ---------------------------------------------------------------------------
# Execute — carry out the plan
# ---------------------------------------------------------------------------


@dataclass
class StepResult:
    step: ConfigurationStep
    success: bool
    skipped: bool
    message: str


def _file_already_has_content(path: Path, content: str) -> bool:
    if not path.is_file():
        return False
    existing = path.read_text(encoding="utf-8", errors="replace")
    return content in existing or content.rstrip("\n") in existing.splitlines()


def _write_file_step(step: ConfigurationStep) -> StepResult:
    assert step.file_path is not None
    assert step.file_content is not None

    if step.kind == "shell-profile":
        return _append_to_profile(step)

    if _file_already_has_content(step.file_path, step.file_content):
        return StepResult(step, success=True, skipped=True, message=f"{step.file_path} already configured")

    step.file_path.parent.mkdir(parents=True, exist_ok=True)
    step.file_path.write_text(step.file_content, encoding="utf-8")
    return StepResult(step, success=True, skipped=False, message=f"Created {step.file_path}")


def _append_to_profile(step: ConfigurationStep) -> StepResult:
    assert step.file_path is not None
    assert step.file_content is not None
    line = step.file_content

    existing = ""
    if step.file_path.is_file():
        existing = step.file_path.read_text(encoding="utf-8", errors="replace")
        if line in existing.splitlines():
            return StepResult(step, success=True, skipped=True, message=f"{step.file_path} already contains this line")

    step.file_path.parent.mkdir(parents=True, exist_ok=True)
    with step.file_path.open("a", encoding="utf-8") as f:
        if existing and not existing.endswith("\n"):
            f.write("\n")
        f.write(line + "\n")
    return StepResult(step, success=True, skipped=False, message=f"Added to {step.file_path}")


def _run_command_step(step: ConfigurationStep) -> StepResult:
    assert step.command is not None
    try:
        result = subprocess.run(step.command, capture_output=True, text=True, timeout=60.0, check=False)
    except (subprocess.SubprocessError, OSError) as e:
        return StepResult(step, success=False, skipped=False, message=f"Failed to run {step.command[0]}: {e}")

    if result.returncode != 0:
        output = (result.stderr or result.stdout or "").strip()
        return StepResult(
            step,
            success=False,
            skipped=False,
            message=f"{' '.join(step.command)} failed (exit {result.returncode}): {output}",
        )

    return StepResult(step, success=True, skipped=False, message=step.description)


def execute_step(step: ConfigurationStep) -> StepResult:
    if step.file_path is not None and step.file_content is not None:
        return _write_file_step(step)
    if step.command is not None:
        return _run_command_step(step)
    return StepResult(step, success=False, skipped=False, message="step has neither file nor command")


def execute_configuration_plan(plan: ConfigurationPlan) -> list[StepResult]:
    results: list[StepResult] = []
    for step in plan.steps:
        results.append(execute_step(step))
    if any(r.success for r in results):
        os.environ[plan.env_var_name] = plan.env_var_value
    return results


def verify_env_var_in_subprocess(name: str, expected: str) -> bool | None:
    """Check whether a new subprocess inherits the expected value.

    Returns True if it matches, False if it doesn't, None if the check
    can't run.  This only confirms the current process environment; it does
    not confirm persistence across login sessions.
    """
    try:
        if is_windows():
            result = subprocess.run(
                ["powershell", "-NoProfile", "-Command", f'[Environment]::GetEnvironmentVariable("{name}", "User")'],
                capture_output=True,
                text=True,
                timeout=15.0,
                check=False,
            )
            if result.returncode == 0:
                return result.stdout.strip() == expected
            return None
        result = subprocess.run(
            ["/bin/sh", "-c", f'echo "${name}"'],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
        return result.returncode == 0 and result.stdout.strip() == expected
    except (subprocess.SubprocessError, OSError):
        return None
