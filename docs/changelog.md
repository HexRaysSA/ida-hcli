# Changelog

All notable changes to HCLI are documented here and in the [GitHub Releases](https://github.com/HexRaysSA/ida-hcli/releases).

This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

!!! tip "Checking Your Version"

    To check which version of HCLI you're currently running:

    ```bash
    hcli --version
    ```

## About This Changelog

This changelog follows the [Keep a Changelog](https://keepachangelog.com/en/1.0.0/) format, organizing changes into these categories:

- **Added** - New features and functionality
- **Changed** - Changes to existing functionality
- **Deprecated** - Soon-to-be removed features
- **Removed** - Now removed features
- **Fixed** - Bug fixes
- **Security** - Security improvements and vulnerability fixes

For complete details including contributors and full diffs, see the [GitHub Releases page](https://github.com/HexRaysSA/ida-hcli/releases).

## [0.12.6] - 2025-10-23

### Added
- Plugin API for settings management

### Fixed
- Network error messaging improvements
- Removed test binary from distribution

[View Release on GitHub](https://github.com/HexRaysSA/ida-hcli/releases/tag/v0.12.6) | [Full Changelog](https://github.com/HexRaysSA/ida-hcli/compare/v0.12.5...v0.12.6)

## [0.12.5] - 2025-10-16

### Added
- `--yes` flag for auto-confirmation on IDA installation
- Enhanced plugin linting features

### Changed
- Improved plugin installation workflow

[View Release on GitHub](https://github.com/HexRaysSA/ida-hcli/releases/tag/v0.12.5) | [Full Changelog](https://github.com/HexRaysSA/ida-hcli/compare/v0.12.4...v0.12.5)

## [0.12.4] - 2025-10-03

### Changed
- Project now published under MIT License

[View Release on GitHub](https://github.com/HexRaysSA/ida-hcli/releases/tag/v0.12.4) | [Full Changelog](https://github.com/HexRaysSA/ida-hcli/compare/v0.12.3...v0.12.4)

---

## Earlier Releases

For releases prior to v0.12.4, please refer to the [GitHub Releases page](https://github.com/HexRaysSA/ida-hcli/releases).

## Upgrade Instructions

### General Upgrade Process

To upgrade HCLI to the latest version:

=== "macOS and Linux"
    ```bash
    curl -LsSf https://hcli.docs.hex-rays.com/install | sh
    ```

=== "Windows"
    ```powershell
    iwr -useb https://hcli.docs.hex-rays.com/install.ps1 | iex
    ```

### Installing a Specific Version

If you need a specific version:

=== "macOS and Linux"
    ```bash
    curl -LsSf https://hcli.docs.hex-rays.com/install | sh -s -- --version 0.12.6
    ```

=== "Windows"
    ```powershell
    iwr https://hcli.docs.hex-rays.com/install.ps1 -OutFile install.ps1
    .\install.ps1 -Version "0.12.6"
    ```

### Breaking Changes

Breaking changes are rare in 0.x versions but will be clearly marked here when they occur. Major version 1.0.0 and beyond will follow strict semantic versioning.

!!! warning "Pre-1.0 Versioning"

    HCLI is currently in 0.x versions. While we aim for stability, breaking changes may occur between minor versions (0.x.0 releases) before reaching 1.0.0.
