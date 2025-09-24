"""Tests for GitHub plugin repository functionality."""

import tempfile
from pathlib import Path

import pytest

from hcli.lib.ida.plugin.repo.github import read_repos_from_file


def test_read_repos_from_file_valid():
    """Test reading valid repository names from a file."""
    content = """# Test repositories
HexRaysSA/ida-hcli
microsoft/vscode

# Another comment
python/cpython
facebook/react
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        repos = read_repos_from_file(f.name)
        
        expected = ['HexRaysSA/ida-hcli', 'microsoft/vscode', 'python/cpython', 'facebook/react']
        assert repos == expected


def test_read_repos_from_file_empty_lines_and_comments():
    """Test that empty lines and comments are properly ignored."""
    content = """

# This is a comment
HexRaysSA/ida-hcli

# Another comment


microsoft/vscode

"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        repos = read_repos_from_file(f.name)
        
        expected = ['HexRaysSA/ida-hcli', 'microsoft/vscode']
        assert repos == expected


def test_read_repos_from_file_invalid_format():
    """Test that invalid repository formats raise ValueError."""
    content = """HexRaysSA/ida-hcli
invalid-repo-without-slash
microsoft/vscode
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        with pytest.raises(ValueError, match="Invalid repository format on line 2"):
            read_repos_from_file(f.name)


def test_read_repos_from_file_empty_owner_or_repo():
    """Test that empty owner or repo names raise ValueError."""
    content = """HexRaysSA/ida-hcli
/empty-owner
empty-repo/
microsoft/vscode
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        with pytest.raises(ValueError, match="Invalid repository format on line 2"):
            read_repos_from_file(f.name)


def test_read_repos_from_file_multiple_slashes():
    """Test that repositories with multiple slashes raise ValueError."""
    content = """HexRaysSA/ida-hcli
invalid/repo/with/multiple/slashes
microsoft/vscode
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        with pytest.raises(ValueError, match="Invalid repository format on line 2"):
            read_repos_from_file(f.name)


def test_read_repos_from_file_nonexistent():
    """Test that reading from a nonexistent file raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="Repository list file not found"):
        read_repos_from_file("/nonexistent/file.txt")


def test_read_repos_from_file_empty_file():
    """Test reading from an empty file returns an empty list."""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write("")
        f.flush()
        
        repos = read_repos_from_file(f.name)
        assert repos == []


def test_read_repos_from_file_only_comments():
    """Test reading from a file with only comments returns an empty list."""
    content = """# Just comments
# No actual repositories
# Another comment
"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.txt', delete=False) as f:
        f.write(content)
        f.flush()
        
        repos = read_repos_from_file(f.name)
        assert repos == []