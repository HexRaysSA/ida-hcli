# Changelog

## unreleased

## [0.15.10] - 2026-01-15

- fix bug paging through GitHub search results #140 @splitline

## [0.15.9] - 2026-01-15

- accept more mimetypes for plugin ZIP archives

## [0.15.8] - 2026-01-13

- Add CHANGELOG.md covering releases since 0.14.1 (12bf8da)
- Add GitHub URL support for plugin install (#138, 55b7e64)

## [0.15.7] - 2026-01-13

### Fixed
- Plugin dependency installation status message nesting

## [0.15.6] - 2026-01-13

### Fixed
- Improved error message when IDA version detection fails

## [0.15.5] - 2026-01-13

### Changed
- Plugin settings now accept `prompt=False` to hide settings with a default value

## [0.15.4] - 2026-01-12

### Fixed
- Handle insufficient disk space errors gracefully
- Better disk status checking for new paths

## [0.15.3] - 2026-01-09

### Changed
- Plugin repositories (GitHub) now accept `content_type=raw` for assets

## [0.15.2] - 2026-01-09

### Added
- Allowed editions field to license data

## [0.15.1] - 2026-01-08

### Added
- `accept-eula` command

## [0.15.0] - 2026-01-06

### Fixed
- Python detection timeout increased for pip
- ZIP paths handled correctly on Windows
- Better detection of `python.exe` on Windows
- Subcommand help docstring formatting

### Changed
- Update notification now shows `hcli update` instead of `uv tool upgrade`

## [0.14.4] - 2026-01-02

### Added
- Repository name normalization

### Fixed
- GitHub URL comparison is now case-insensitive

## [0.14.3] - 2025-12-26

### Added
- `get_bucket` functionality

### Fixed
- Warning for IDA 9.2/Linux paths with spaces

## [0.14.2] - 2025-11-26

### Fixed
- Better resolution of current plugin in settings

## [0.14.1] - 2025-11-26

### Added
- Support for `$IDADIR` environment variable

### Fixed
- Incorrect OS references
- Additional GitHub rate limiting edge cases
