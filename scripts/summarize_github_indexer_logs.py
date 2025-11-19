#!/usr/bin/env python3
"""Parse HCLI GitHub indexing logs and display results hierarchically.

This script parses structured logging output from the HCLI GitHub indexing
process, organizing messages by repository, release/tag, archive URL, and
metadata paths.
"""

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class LogMessage:
    """A single log message with its metadata."""

    message: str
    data: dict[str, Any]
    level: str = "DEBUG"

    @property
    def is_error(self) -> bool:
        return "error" in self.data

    @property
    def is_success(self) -> bool:
        msg_lower = self.message.lower()
        return "found valid" in msg_lower and "failed" not in msg_lower and not self.is_error


@dataclass
class MetadataPath:
    """Represents a metadata file found in an archive."""

    path: str
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class Archive:
    """Represents an archive (zip/tarball) being indexed."""

    url: str
    archive_type: str
    metadata_paths: dict[str, MetadataPath] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class ReleaseOrTag:
    """Represents a release, tag, or commit."""

    identifier: str
    ref_type: str
    archives: dict[str, Archive] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


@dataclass
class Repository:
    """Represents a GitHub repository."""

    owner: str
    repo: str
    releases: dict[str, ReleaseOrTag] = field(default_factory=dict)
    messages: list[LogMessage] = field(default_factory=list)


class LogParser:
    """Parser for HCLI GitHub indexing logs."""

    def __init__(self):
        self.repos: dict[tuple[str, str], Repository] = {}

    def parse_file(self, log_path: Path) -> None:
        """Parse a log file and build the hierarchical structure."""
        content = log_path.read_text()

        pattern = r"<structured:\s*(\{[^>]+\})>"
        matches = re.findall(pattern, content, re.DOTALL)

        for match in matches:
            json_str = " ".join(match.split())
            try:
                data = json.loads(json_str)
                self._process_log_entry(data)
            except json.JSONDecodeError:
                continue

    def _process_log_entry(self, data: dict[str, Any]) -> None:
        """Process a single log entry and add it to the hierarchy."""
        if "owner" not in data or "repo" not in data:
            return

        owner = data["owner"]
        repo = data["repo"]
        message = data.get("message", "")

        repo_key = (owner, repo)
        if repo_key not in self.repos:
            self.repos[repo_key] = Repository(owner=owner, repo=repo)

        repository = self.repos[repo_key]
        log_msg = LogMessage(message=message, data=data)

        ref_key = self._get_ref_key(data)
        if ref_key:
            ref_identifier, ref_type = ref_key
            if ref_identifier not in repository.releases:
                repository.releases[ref_identifier] = ReleaseOrTag(identifier=ref_identifier, ref_type=ref_type)

            release = repository.releases[ref_identifier]

            url = data.get("url")
            if url:
                if url not in release.archives:
                    archive_type = data.get("type", "unknown")
                    release.archives[url] = Archive(url=url, archive_type=archive_type)

                archive = release.archives[url]

                path = data.get("path")
                if path:
                    if path not in archive.metadata_paths:
                        archive.metadata_paths[path] = MetadataPath(path=path)

                    metadata = archive.metadata_paths[path]
                    metadata.messages.append(log_msg)
                else:
                    archive.messages.append(log_msg)
            else:
                release.messages.append(log_msg)
        else:
            repository.messages.append(log_msg)

    def _get_ref_key(self, data: dict[str, Any]) -> tuple[str, str] | None:
        """Extract release/tag/commit identifier and type from log data."""
        if "release" in data:
            return (data["release"], "release")
        elif "tag" in data:
            return (data["tag"], "tag")
        elif "commit" in data:
            return (data["commit"], "commit")
        return None


class LogRenderer:
    """Renders parsed logs in a hierarchical format."""

    def __init__(self, parser: LogParser):
        self.parser = parser

    def render(self) -> None:
        """Render all repositories and their data."""
        repos = sorted(self.parser.repos.values(), key=lambda r: (r.owner, r.repo))

        print(f"\n{'=' * 80}")
        print("GitHub Indexing Log Analysis")
        print(f"{'=' * 80}")
        print(f"Total repositories: {len(repos)}\n")

        for repo in repos:
            self._render_repository(repo)

    def _render_repository(self, repo: Repository) -> None:
        """Render a single repository and all its releases."""
        print(f"\n{repo.owner}/{repo.repo}")
        print(f"{'-' * 80}")

        if repo.messages:
            print("  Repository-level messages:")
            for msg in repo.messages:
                self._render_message(msg, indent=4)

        if not repo.releases:
            print("  No releases/tags found")
            return

        for release in sorted(repo.releases.values(), key=lambda r: r.identifier):
            self._render_release(release)

    def _render_release(self, release: ReleaseOrTag) -> None:
        """Render a release/tag and all its archives."""
        print(f"\n  [{release.ref_type.upper()}] {release.identifier}")

        if release.messages:
            for msg in release.messages:
                self._render_message(msg, indent=4)

        for archive in release.archives.values():
            self._render_archive(archive)

    def _render_archive(self, archive: Archive) -> None:
        """Render an archive and all its metadata paths."""
        print(f"\n    Archive ({archive.archive_type}):")
        print(f"      URL: {archive.url}")

        if archive.messages:
            for msg in archive.messages:
                self._render_message(msg, indent=6)

        if archive.metadata_paths:
            for metadata in archive.metadata_paths.values():
                self._render_metadata_path(metadata)

    def _render_metadata_path(self, metadata: MetadataPath) -> None:
        """Render a metadata path and all its messages."""
        print(f"\n      Metadata: {metadata.path}")

        errors = [m for m in metadata.messages if m.is_error]
        successes = [m for m in metadata.messages if m.is_success]
        other = [m for m in metadata.messages if not m.is_error and not m.is_success]

        if successes:
            print("        ✓ Successes:")
            for msg in successes:
                self._render_message(msg, indent=10)

        if errors:
            print("        ✗ Errors:")
            for msg in errors:
                self._render_message(msg, indent=10)

        if other:
            print("        • Other:")
            for msg in other:
                self._render_message(msg, indent=10)

    def _render_message(self, msg: LogMessage, indent: int = 0) -> None:
        """Render a single log message."""
        prefix = " " * indent
        print(f"{prefix}• {msg.message}")

        if msg.is_error and "error" in msg.data:
            error_text = msg.data["error"]
            for line in error_text.split("\n"):
                if line.strip():
                    print(f"{prefix}  {line}")

        interesting_keys = [
            "plugin_name",
            "plugin_version",
            "asset",
        ]
        for key in interesting_keys:
            if key in msg.data:
                print(f"{prefix}  {key}: {msg.data[key]}")


def main() -> None:
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <log_file>")
        sys.exit(1)

    log_path = Path(sys.argv[1])
    if not log_path.exists():
        print(f"Error: File not found: {log_path}")
        sys.exit(1)

    parser = LogParser()
    parser.parse_file(log_path)

    renderer = LogRenderer(parser)
    renderer.render()


if __name__ == "__main__":
    main()
