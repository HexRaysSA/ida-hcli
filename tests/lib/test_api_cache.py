"""Tests for API client cache functionality."""

import os
import shutil
import tempfile

import pytest

from hcli.lib.util.cache import get_cache_directory


@pytest.fixture
def temp_cache_dir():
    """Create a temporary cache directory for testing."""
    temp_dir = tempfile.mkdtemp()
    old_cache = os.environ.get("HCLI_CACHE_DIR", "")
    os.environ["HCLI_CACHE_DIR"] = temp_dir
    try:
        yield temp_dir
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
        if old_cache:
            os.environ["HCLI_CACHE_DIR"] = old_cache
        else:
            os.environ.pop("HCLI_CACHE_DIR", None)


def test_cache_path_construction_for_download(temp_cache_dir):
    """Test that cache path is constructed correctly for downloads using full asset keys."""

    # Test with full asset keys (not just filenames)
    test_cases = [
        "release/9.2/ida-pro/ida-pro_92_armmac.app.zip",
        "release/9.1/ida-pro/ida-pro_91_x64linux.run",
        "release/9.0/ida-sdk/ida-sdk-9.0.zip",
    ]

    for asset_key in test_cases:
        # This is how the download_file method constructs the cache path
        cache_path = get_cache_directory("downloads") / asset_key

        # Verify the cache path includes the full asset key structure
        assert asset_key in str(cache_path), f"Cache path should contain full asset key: {cache_path}"

        # Verify it's within the downloads directory
        assert "downloads" in cache_path.parts, f"Cache path should be in downloads directory: {cache_path}"
