from hcli.commands.license.common import downloadable_asset_types, uses_legacy_license_format
from hcli.lib.ida import IdaProduct

BOTH = ["hexlic", "hexlic_v90"]


def test_legacy_license_format_skipped_by_default():
    assert downloadable_asset_types(BOTH) == ["hexlic"]


def test_legacy_license_format_included_on_request():
    """--include-legacy-license downloads the IDA 9.0 format too (#429)."""
    assert downloadable_asset_types(BOTH, "include") == BOTH


def test_legacy_license_format_preferred_replaces_the_current_format():
    """IDA before 9.3 rejects a license with add-ons it does not know (#429)."""
    assert downloadable_asset_types(BOTH, "prefer") == ["hexlic_v90"]
    assert downloadable_asset_types(["hexlic"], "prefer") == ["hexlic"]


def test_legacy_license_format_used_before_ida_9_3():
    def version(major: int, minor: int) -> IdaProduct:
        return IdaProduct(product="IDA Professional", major=major, minor=minor)

    assert uses_legacy_license_format(version(9, 2))
    assert not uses_legacy_license_format(version(9, 3))
