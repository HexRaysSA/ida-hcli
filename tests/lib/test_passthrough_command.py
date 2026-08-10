"""Tests for commands that hand their arguments to another program."""

from __future__ import annotations

import pytest
import rich_click as click
from click.testing import CliRunner

from hcli.lib.commands import PassthroughCommand


@click.command(cls=PassthroughCommand)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def echo_args(args: tuple[str, ...]) -> None:
    """Docstring for echo-args."""
    click.echo(repr(list(args)))


@click.command(cls=PassthroughCommand)
@click.argument("name")
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def echo_named_args(name: str, args: tuple[str, ...]) -> None:
    """Docstring for echo-named-args."""
    click.echo(f"{name}: {list(args)!r}")


def invoke(command: click.Command, argv: list[str]):
    result = CliRunner().invoke(command, argv)
    assert result.exit_code == 0, result.output
    return result.output.strip()


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        ([], []),
        (["-m", "pip", "--help"], ["-m", "pip", "--help"]),
        (["-c", "print(1)"], ["-c", "print(1)"]),
        (["script.py", "--flag"], ["script.py", "--flag"]),
        # options HCLI defines elsewhere aren't HCLI's here
        (["--version"], ["--version"]),
        (["-mpip", "--help"], ["-mpip", "--help"]),
    ],
)
def test_arguments_reach_the_program(argv, expected):
    assert invoke(echo_args, argv) == repr(expected)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        # the leading `--` marks where the program's arguments start, so it is dropped
        (["--", "--help"], ["--help"]),
        (["--", "--version"], ["--version"]),
        # doubling it passes one along
        (["--", "--", "--version"], ["--", "--version"]),
        # later separators belong to the program
        (["script.py", "--", "-x"], ["script.py", "--", "-x"]),
    ],
)
def test_separator_marks_where_the_program_arguments_start(argv, expected):
    assert invoke(echo_args, argv) == repr(expected)


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["capa"], []),
        (["capa", "--version"], ["--version"]),
        (["capa", "--", "--version"], ["--version"]),
        (["capa", "-q", "--", "-x"], ["-q", "--", "-x"]),
    ],
)
def test_arguments_after_a_required_one(argv, expected):
    assert invoke(echo_named_args, argv) == f"capa: {expected!r}"


def test_leading_help_describes_the_command():
    assert "Docstring for echo-args." in invoke(echo_args, ["--help"])


def test_required_argument_is_still_required():
    result = CliRunner().invoke(echo_named_args, [])
    assert result.exit_code != 0
    assert "NAME" in result.output
