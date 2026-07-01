"""Entry point for ``python -m hcli``.

Lets the CLI be launched via the package name (the conventional, discoverable
form) in addition to the ``hcli`` console script — e.g. when only an interpreter
path is known. ``get_hcli_executable_path`` relies on this for its fallback.
"""

from hcli.main import cli

if __name__ == "__main__":
    cli()
