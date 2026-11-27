"""pythonDependencies entries must be requirements, never pip options (issue #474)."""

from __future__ import annotations

import io
import json
import subprocess
import zipfile
from pathlib import Path

import pytest
from click.testing import CliRunner
from fixtures import *
from fixtures import make_test_install_context
from pydantic import ValidationError

from hcli.commands.plugin import plugin as plugin_group
from hcli.commands.plugin.bundle import _download_wheelhouse
from hcli.lib.ida.plugin import IDAMetadataDescriptor, get_metadata_from_plugin_archive, parse_pep723_metadata
from hcli.lib.ida.plugin.bundle import PipTarget
from hcli.lib.ida.plugin.install import apply_install, install_plugin_archive, is_plugin_installed
from hcli.lib.ida.plugin.result import InstallStatus
from hcli.lib.ida.python import PIP_OPTIONS_DEFAULT, pip_install_packages, verify_pip_can_install_packages

BAD_DEPENDENCIES = ["--target=x", "foo"]

INLINE_ENTRY_POINT = """\
# /// script
# dependencies = [
#   "--target=x",
#   "foo",
# ]
# ///
"""


def _make_metadata(python_dependencies: list[str] | str) -> dict:
    return {
        "IDAMetadataDescriptorVersion": 1,
        "plugin": {
            "name": "my-plugin",
            "version": "1.0.0",
            "entryPoint": "my-plugin.py",
            "urls": {"repository": "https://github.com/example/my-plugin"},
            "authors": [{"name": "Test", "email": "test@example.com"}],
            "pythonDependencies": python_dependencies,
        },
    }


def _make_plugin_files(python_dependencies: list[str] | str) -> dict[str, str]:
    entry_point = INLINE_ENTRY_POINT if python_dependencies == "inline" else "# plugin\n"
    return {
        "my-plugin/ida-plugin.json": json.dumps(_make_metadata(python_dependencies)),
        "my-plugin/my-plugin.py": entry_point,
    }


def _make_zip(files: dict[str, str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


@pytest.mark.parametrize("dependency", ["--target=x", "-e.", "--index-url=https://example.com", "not a requirement"])
def test_metadata_rejects_dependency_that_is_not_a_requirement(dependency):
    with pytest.raises(ValidationError, match="invalid Python dependency"):
        IDAMetadataDescriptor.model_validate(_make_metadata([dependency, "foo"]))


@pytest.mark.parametrize(
    "dependency",
    ["foo", "foo[bar]>=1.0,<2", 'foo; python_version < "3.12"', "foo @ https://example.com/foo-1.0.tar.gz"],
)
def test_metadata_accepts_requirements(dependency):
    descriptor = IDAMetadataDescriptor.model_validate(_make_metadata([dependency]))
    assert descriptor.plugin.python_dependencies == [dependency]


def test_metadata_accepts_inline():
    descriptor = IDAMetadataDescriptor.model_validate(_make_metadata("inline"))
    assert descriptor.plugin.python_dependencies == "inline"


def test_parse_pep723_metadata_rejects_dependency_that_is_not_a_requirement():
    with pytest.raises(ValueError, match="invalid Python dependency: '--target=x'"):
        parse_pep723_metadata(INLINE_ENTRY_POINT)


def _forbid_pip(monkeypatch) -> None:
    def _must_not_run(*args, **kwargs):
        raise AssertionError("pip must not run for a plugin with an invalid Python dependency")

    monkeypatch.setattr("hcli.lib.ida.python.subprocess.run", _must_not_run)


def test_install_rejects_list_dependency_before_pip_runs(virtual_ida_environment, monkeypatch):
    _forbid_pip(monkeypatch)

    archive = _make_zip(_make_plugin_files(BAD_DEPENDENCIES))
    with pytest.raises(ValueError, match="Invalid plugin archive"):
        install_plugin_archive(archive, "my-plugin", make_test_install_context())

    assert not is_plugin_installed("my-plugin")


def test_install_rejects_inline_dependency_before_pip_runs(virtual_ida_environment, monkeypatch):
    _forbid_pip(monkeypatch)

    archive = _make_zip(_make_plugin_files("inline"))
    _, metadata = get_metadata_from_plugin_archive(archive, "my-plugin")
    result = apply_install(source=archive, plugin_name="my-plugin", metadata=metadata, ctx=make_test_install_context())

    assert result.status == InstallStatus.FAILED
    assert result.reason is not None
    assert "--target=x" in result.reason
    assert not is_plugin_installed("my-plugin")


@pytest.mark.parametrize("as_archive", [False, True])
@pytest.mark.parametrize("python_dependencies", [BAD_DEPENDENCIES, "inline"])
def test_lint_reports_dependency_that_is_not_a_requirement(tmp_path, python_dependencies, as_archive):
    files = _make_plugin_files(python_dependencies)
    if as_archive:
        target = tmp_path / "my-plugin.zip"
        target.write_bytes(_make_zip(files))
    else:
        for name, content in files.items():
            (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
            (tmp_path / name).write_text(content)
        target = tmp_path / "my-plugin"

    result = CliRunner(mix_stderr=False).invoke(plugin_group, ["lint", str(target)])

    assert result.exit_code != 0, result.output
    assert "--target=x" in result.output


def _capture_argv(monkeypatch) -> list[list[str]]:
    calls: list[list[str]] = []

    def _fake_run(argv, *args, **kwargs):
        calls.append(list(argv))
        return subprocess.CompletedProcess(args=argv, returncode=0, stdout=b"", stderr=b"")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    return calls


@pytest.mark.parametrize("pip_function", [pip_install_packages, verify_pip_can_install_packages])
def test_pip_argv_ends_options_before_packages(monkeypatch, tmp_path, pip_function):
    calls = _capture_argv(monkeypatch)

    pip_function(tmp_path / "python3", ["foo", "bar"], constraints=["baz<2"])

    (argv,) = calls
    assert argv[-3:] == ["--", "foo", "bar"]
    assert "--constraint" in argv[: argv.index("--")]


def test_pip_download_argv_ends_options_before_packages(monkeypatch, tmp_path):
    calls = _capture_argv(monkeypatch)

    _download_wheelhouse(
        Path("uv"), ["foo", "bar"], PipTarget.parse("linux-x86_64-cp312"), tmp_path, PIP_OPTIONS_DEFAULT
    )

    (argv,) = calls
    assert argv[-3:] == ["--", "foo", "bar"]
