from __future__ import annotations

import rich_click as click


@click.group()
@click.option(
    "--no-python-environment-check",
    is_flag=True,
    help="Do not warn when IDA's Python environment differs from the recommended setup.",
)
@click.pass_context
def python(ctx: click.Context, no_python_environment_check: bool) -> None:
    """Use the Python environment that IDA loads."""
    ctx.ensure_object(dict)
    ctx.obj["no_python_environment_check"] = no_python_environment_check


from .create_environment import create_environment
from .doctor import doctor
from .exec_python import exec_python
from .explain_environment import explain_environment
from .find_script import find_script
from .run_script import run_script

python.add_command(create_environment, name="create-environment")
python.add_command(doctor, name="doctor")
python.add_command(exec_python, name="exec")
python.add_command(explain_environment, name="explain-environment")
python.add_command(find_script, name="find-script")
python.add_command(run_script, name="run-script")
