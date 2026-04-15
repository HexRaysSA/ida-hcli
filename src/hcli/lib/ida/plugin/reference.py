"""Parsing and normalization helpers for plugin references.

A plugin reference is a user-supplied string that names a plugin and may
optionally qualify it with a version spec and/or a repository URL (host).

Accepted forms:
    name
    name==1.2.3
    name@https://github.com/org/repo
    name==1.2.3@https://github.com/org/repo

The module is intentionally free of Click/Rich so it can be unit tested easily.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse

# whole-string match for a GitHub repository URL in the shape allowed by
# ``ida-plugin.json`` (see ``URLs.validate_github_url`` in
# ``src/hcli/lib/ida/plugin/__init__.py``). Trailing slash optional.
_GITHUB_REPO_RE = re.compile(r"^https://github\.com/[a-zA-Z0-9._-]+/[a-zA-Z0-9._-]+/?$", re.IGNORECASE)


@dataclass(frozen=True)
class PluginReference:
    """A parsed plugin reference.

    Attributes:
        name: bare plugin name.
        version_spec: version specifier with operator (``"==1.2.3"``), or ``""`` when absent.
        host: normalized repository URL, or ``None`` when unqualified.
    """

    name: str
    version_spec: str
    host: str | None


def is_github_repository_url(value: str) -> bool:
    """Return True if ``value`` is a whole-string GitHub repository URL.

    This differs from ``repo/github.py:is_github_url`` which only checks
    whether ``github.com/`` appears somewhere in the string. A qualified
    plugin reference like ``foo@https://github.com/org/repo`` must parse as
    a reference, not a raw URL, so we use a strict whole-string match here.
    """
    return bool(_GITHUB_REPO_RE.match(value))


def normalize_plugin_host(host: str) -> str:
    """Normalize a plugin repository URL for comparison.

    The normalization:
    - lowercases scheme, netloc, and path;
    - strips exactly one trailing slash from the path.

    Args:
        host: repository URL string, for example ``https://GitHub.com/Org/Repo/``.

    Returns:
        normalized URL string, for example ``https://github.com/org/repo``.

    Raises:
        ValueError: when ``host`` cannot be parsed as an absolute URL with scheme and netloc.
    """
    parsed = urlparse(host)
    if not parsed.scheme or not parsed.netloc:
        raise ValueError(f"invalid plugin host URL: {host!r}")

    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()
    path = path.removesuffix("/")

    return f"{scheme}://{netloc}{path}"


def _split_name_and_version(value: str) -> tuple[str, str]:
    """Split ``name[op version]`` into ``(name, version_spec)``.

    Returns ``(value, "")`` when there's no version spec. The version spec is
    returned including the operator (``"==1.2.3"``) so callers can round-trip
    the original syntax.

    Raises:
        ValueError: when the operator looks malformed (e.g. trailing ``=``
            without another character).
    """
    # look for the first operator char
    match = re.search(r"[=><!~]", value)
    if not match:
        return value, ""

    op_start = match.start()
    name = value[:op_start]
    op_chars = value[op_start : op_start + 2]
    if len(op_chars) < 2 or op_chars[1] != "=":
        raise ValueError(f"invalid plugin version spec: {value!r}")

    # operator + remaining characters (the version string)
    version_spec = value[op_start:]
    return name, version_spec


def parse_plugin_reference(value: str) -> PluginReference:
    """Parse a plugin reference string.

    Supported forms:
        name
        name==1.2.3
        name@https://github.com/org/repo
        name==1.2.3@https://github.com/org/repo

    The version operator, when present, appears before the ``@repo`` suffix.
    The repository suffix is detected by splitting on the last ``@`` and
    checking whether the suffix is a whole-string GitHub repository URL.
    A string that is itself a raw GitHub repository URL is NOT treated as a
    plugin reference and raises ``ValueError``.

    Returns:
        a ``PluginReference`` with ``host`` normalized when present.

    Raises:
        ValueError: when the string cannot be parsed (empty name, malformed
            operator, etc.).
    """
    if not value:
        raise ValueError("plugin reference is empty")

    if is_github_repository_url(value):
        # the whole string is a raw GitHub URL, not a plugin reference
        raise ValueError(f"value is a raw GitHub URL, not a plugin reference: {value!r}")

    host: str | None = None
    remaining = value
    if "@" in value:
        left, _, right = value.rpartition("@")
        if is_github_repository_url(right):
            host = normalize_plugin_host(right)
            remaining = left

    name, version_spec = _split_name_and_version(remaining)
    if not name:
        raise ValueError(f"plugin reference has empty name: {value!r}")

    return PluginReference(name=name, version_spec=version_spec, host=host)


def format_qualified_plugin_reference(
    ref: PluginReference | tuple[str, str, str | None],
) -> str:
    """Render a plugin reference in its canonical user-facing string form.

    Formats:
        name@repo
        name==1.2.3@repo

    The ``host`` must be provided for the output to be useful; callers that
    only have a bare name/version don't need this helper.
    """
    if isinstance(ref, PluginReference):
        name, version_spec, host = ref.name, ref.version_spec, ref.host
    else:
        name, version_spec, host = ref

    if not host:
        if version_spec:
            return f"{name}{version_spec}"
        return name

    if version_spec:
        return f"{name}{version_spec}@{host}"
    return f"{name}@{host}"
