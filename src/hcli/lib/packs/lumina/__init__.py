"""Pushing Lumina packs to a Lumina server, in Python.

The rules are the pack builder's importer contract, the same its own pushes
follow; keep them in line:

- ``arrow``: reads a pack member (a Lumina Arrow IPC file), strictly.
- ``pack``: reads a pack (zstd, then plain ustar) and checks it against the Release.
- ``push``: filters the rows and posts them to ``/api/metadata`` in batches.
- ``importer``: verifies every member before the first request, then pushes.
"""
