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
    """Test that cache path is constructed correctly for downloads."""
    from hcli.lib.util.string import slugify

    # Test various filenames that would be downloaded
    test_cases = [
        ("ida-pro_91_x64linux.run", "ida_pro_91_x64linux_run"),
        ("ida-sdk-9.1.zip", "ida_sdk_9_1_zip"),
        ("idat64", "idat64"),
    ]

    for filename, expected_slug in test_cases:
        # This is how the download_file method constructs the cache path
        slug = slugify(filename, separator="_")
        cache_dir = get_cache_directory("downloads", slug)

        # Verify the slug is correct
        assert slug == expected_slug, f"Expected slug '{expected_slug}' but got '{slug}'"

        # Verify the cache directory structure
        path_parts = cache_dir.parts
        assert "downloads" in path_parts, f"Cache path should contain 'downloads' directory: {cache_dir}"
        assert slug in path_parts, f"Cache path should contain slug '{slug}': {cache_dir}"

        # Verify cache directory exists (created by get_cache_directory)
        assert cache_dir.exists(), f"Cache directory should exist: {cache_dir}"


def test_cache_directory_structure():
    """Test that cache directory uses the correct structure."""
    # This test verifies the expected structure without making actual API calls
    # The structure should be: {XDG_CACHE_HOME}/hex-rays/hcli/downloads/{slug}/
    filename = "ida-pro_91_x64linux.run"

    # Get the cache directory with "downloads" key and slug
    from hcli.lib.util.string import slugify

    slug = slugify(filename, separator="_")
    cache_dir = get_cache_directory("downloads", slug)

    # Verify the path contains expected components
    path_parts = cache_dir.parts
    assert "downloads" in path_parts, "Cache path should contain 'downloads' directory"
    assert slug in path_parts, f"Cache path should contain slug '{slug}'"
    assert cache_dir.exists(), "Cache directory should be created"


def test_slug_generation():
    """Test that slugification works correctly for various filenames."""
    from hcli.lib.util.string import slugify

    test_cases = [
        ("ida-pro_91_x64linux.run", "ida_pro_91_x64linux_run"),
        ("IDA Pro 9.1.exe", "ida_pro_9_1_exe"),
        ("file-with-dashes.zip", "file_with_dashes_zip"),
        ("FILE_WITH_UNDERSCORES.tar.gz", "file_with_underscores_tar_gz"),
    ]

    for input_name, expected_slug in test_cases:
        result = slugify(input_name, separator="_")
        assert result == expected_slug, f"Expected slug '{expected_slug}' but got '{result}' for input '{input_name}'"
