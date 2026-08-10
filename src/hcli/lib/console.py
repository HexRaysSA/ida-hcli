import json
from typing import Any

from rich.console import Console

# Deliberately unpinned: with no `file`, Rich resolves sys.stdout/sys.stderr on
# every write, so redirection by a caller (or a test harness) is honored.
console = Console()
stderr_console = Console(stderr=True)


def print_json(data: Any) -> None:
    """Print `data` as JSON via `console`, not `click.echo`.

    `click.echo` resolves its output stream independently of `console`, so it
    isn't covered by the stream pinning the test harness applies around
    `CliRunner` and can write to a stale stdout handle (see #190). Markup and
    highlighting are disabled so JSON's own brackets aren't parsed as Rich
    markup, and soft_wrap avoids Rich reflowing long lines.
    """
    console.print(json.dumps(data, indent=2), markup=False, highlight=False, soft_wrap=True)
