from __future__ import annotations

import rich_click as click


@click.group()
def python() -> None:
    """Use the Python environment that IDA loads."""


from .exec_python import exec_python
from .explain_environment import explain_environment
from .find_script import find_script
from .run_script import run_script

python.add_command(exec_python, name="exec")
python.add_command(explain_environment, name="explain-environment")
python.add_command(find_script, name="find-script")
python.add_command(run_script, name="run-script")
