from __future__ import annotations

import rich_click as click


@click.group()
def ida() -> None:
    """Manage IDA installations."""
    pass


from .install import install  # noqa: E402

ida.add_command(install)
