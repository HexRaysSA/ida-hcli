from __future__ import annotations

import rich_click as click

from hcli.lib.packs.models import PackFormat


@click.group()
def packs() -> None:
    """Lumina and FLIRT data pack commands.

    The packs come from the portal's `packs` bucket, with hcli's login
    (`hcli login`, or HCLI_API_KEY with the assets:read scope).

    Each format has its own command group (`lumina`, `flirt`) over its own
    stable Release; `doctor` checks access to both.
    """


@packs.group()
def lumina() -> None:
    """Lumina packs: list, inspect, download, and push them to a Lumina server.

    Packs come from the stable Lumina Release on the portal.

    push and sync push packs to a private Lumina server
    (HCLI_LUMINA_SERVER, key in LUMINA_API_KEY).
    """


@packs.group()
def flirt() -> None:
    """FLIRT signature packs: list, inspect, download, and install them for IDA.

    Packs come from the stable FLIRT Release on the portal.

    install and sync install packs into an IDA signature folder
    (IDA's user one, $IDAUSR/sig, unless --sig-dir names another).
    """


from .doctor import doctor
from .flirt_sync import sync as flirt_sync
from .get import get_command
from .install import install
from .list import list_command
from .push import push
from .show import show_command
from .sync import sync

for _group, _fmt in ((lumina, PackFormat.LUMINA), (flirt, PackFormat.FLIRT)):
    _group.add_command(list_command(_fmt))
    _group.add_command(show_command(_fmt))
    _group.add_command(get_command(_fmt))
lumina.add_command(push)
lumina.add_command(sync)
flirt.add_command(install)
flirt.add_command(flirt_sync)
packs.add_command(doctor)
