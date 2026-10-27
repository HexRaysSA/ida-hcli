from hcli.commands.license.common import downloadable_asset_types
from hcli.lib.api.license import License


def _license(asset_types: list[str] | None) -> License:
    return License.model_construct(asset_types=asset_types)


def test_legacy_license_format_skipped_by_default():
    assert downloadable_asset_types(_license(["hexlic", "hexlic_v90"])) == ["hexlic"]


def test_legacy_license_format_included_on_request():
    """--include-legacy-license downloads the IDA 9.0 format too (#429)."""
    assert downloadable_asset_types(_license(["hexlic", "hexlic_v90"]), include_legacy=True) == ["hexlic", "hexlic_v90"]


def test_license_without_assets():
    assert downloadable_asset_types(_license(None), include_legacy=True) == []
