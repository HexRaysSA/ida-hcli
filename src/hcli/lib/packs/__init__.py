"""Client for the Lumina/FLIRT data packs, read through the portal's asset API.

The pack builder publishes data packs into an apt-like layout, one independent root per
format (``lumina/``, ``flirt/``); the portal registers each file as an asset of
its ``packs`` bucket (see ``repository``)::

    <format>/release                                        the stable Release (JSON index)
    <format>/<component>/<platform>-<group>-<arch>/pack      a pack archive
    <format>/<component>/<platform>-<group>-<arch>/contents  its contents sidecar

The wire contract ("Public distribution Release v1") is owned by the pack
builder; these modules read it leniently and never write it.
"""
