"""MkDocs hooks that prepare the hcli command tree for the mkdocs-click page at docs/reference/cli.md.

The changes apply only to the docs build process.
"""

import rich_click as click

from hcli.main import DESCRIPTION, cli


def _use_registered_names(group: click.Group) -> None:
    """Rename each subcommand to the name that its group registers it under.

    mkdocs-click titles each command with `command.name`, which is the function
    name when a command is registered with `add_command(cmd, name=...)`.
    """
    for name, command in group.commands.items():
        command.name = name
        if isinstance(command, click.Group):
            _use_registered_names(command)


def on_config(config):
    # the runtime help text shows local status (account, IDA path) and Rich markup
    cli.help = DESCRIPTION
    # `extension` is an alias of `plugin` and would repeat the full plugin tree
    cli.commands["extension"].hidden = True
    _use_registered_names(cli)
    return config
