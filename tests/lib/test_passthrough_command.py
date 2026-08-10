"""Tests for commands that hand their arguments to another program."""

from __future__ import annotations

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


def test_options_reach_the_program():
    assert invoke(echo_args, ["-m", "pip", "--help"]) == "['-m', 'pip', '--help']"
    assert invoke(echo_args, ["-c", "print(1)"]) == "['-c', 'print(1)']"
    assert invoke(echo_args, ["script.py", "--flag"]) == "['script.py', '--flag']"


def test_hcli_options_reach_the_program():
    """Options HCLI defines elsewhere aren't HCLI's here."""
    assert invoke(echo_args, ["--version"]) == "['--version']"
    assert invoke(echo_args, ["-mpip", "--help"]) == "['-mpip', '--help']"


def test_leading_help_describes_the_command():
    assert "Docstring for echo-args." in invoke(echo_args, ["--help"])


def test_separator_is_dropped():
    """`--` marks where the program's arguments start, so the program doesn't see it."""
    assert invoke(echo_args, ["--", "--help"]) == "['--help']"
    assert invoke(echo_args, ["--", "--version"]) == "['--version']"


def test_separator_can_be_passed_along():
    assert invoke(echo_args, ["--", "--", "--version"]) == "['--', '--version']"


def test_later_separators_belong_to_the_program():
    assert invoke(echo_args, ["script.py", "--", "-x"]) == "['script.py', '--', '-x']"
    assert invoke(echo_named_args, ["capa", "-q", "--", "-x"]) == "capa: ['-q', '--', '-x']"


def test_no_arguments():
    assert invoke(echo_args, []) == "[]"


def test_arguments_after_a_required_one():
    assert invoke(echo_named_args, ["capa", "--version"]) == "capa: ['--version']"
    assert invoke(echo_named_args, ["capa", "--", "--version"]) == "capa: ['--version']"
    assert invoke(echo_named_args, ["capa"]) == "capa: []"
    assert "Docstring for echo-named-args." in invoke(echo_named_args, ["--help"])


def test_required_argument_is_still_required():
    result = CliRunner().invoke(echo_named_args, [])
    assert result.exit_code != 0
    assert "NAME" in result.output
