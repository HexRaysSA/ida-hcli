# Changelog

## unreleased

### Added
- `hcli plugin bundle create` accepts bare and range plugin specs, and resolves them and unpinned plugin dependencies to the newest version for each target platform
- Support an optional `requiresPython` field in `ida-plugin.json` and check it against IDA's Python environment before installation
- `hcli plugin install`, `hcli plugin upgrade`, plugin dependency installation, and `hcli plugin bundle create` skip plugin versions whose `requiresPython` excludes the target Python version, and select the newest version that is compatible. When IDA's Python cannot be detected, `hcli plugin install` and `hcli plugin upgrade` skip only the versions that declare `requiresPython`
- `hcli plugin bundle create` selects, for each target cell, the newest plugin version whose required plugin dependencies can also be resolved for that cell, and goes back to older versions when two plugins pin different versions of one dependency
- `hcli plugin bundle create` accepts a `repo/` prefix on a plugin spec to select the plugin from a configured plugin repository. The spec and its dependencies resolve across all configured repositories
- Recursive plugin dependency installation: dependencies that declare their own dependencies are resolved transitively
- `hcli plugin install` selects, for the current IDA installation, the newest version of the plugin whose required plugin dependencies can also be resolved, and goes back to older versions of the plugin or of a dependency when a newer one cannot be satisfied
- `hcli plugin upgrade` selects the newest version newer than the installed one whose required plugin dependencies can also be resolved. When no newer version can be installed and the installed version matches the requested version, it reports that the plugin is up to date and prints why each newer version was rejected. Otherwise the upgrade fails
- Add `--allowed-editions` to `hcli asset put` to gate an asset by licence edition, addon code, or `any_edition`
- Warn when IDA's Python version (registered by idapyswitch) doesn't match the active virtualenv, in `explain-environment` and before installing plugin dependencies
- Honor `$IDAPYTHON_VENV_EXECUTABLE` for plugin dependency management
- Add GitHub Copilot CLI support to `hcli mcp install`
- Add `hcli extension` as an alias for `hcli plugin`, so `hcli extension install <plugin>` uses the plugin manager

### Fixed
- Check `requiresPython` of a plugin dependency before installing its Python packages
- `hcli plugin install` and `hcli plugin upgrade` resolve the plugin and all of its plugin dependencies before they write any file, and fail without writing when a required plugin dependency cannot be resolved
- `hcli plugin install` installs the plugin dependencies of the components of a plugin
- `hcli plugin install` fetches plugin dependencies from a plugin bundle that is a configured plugin repository
- `hcli plugin bundle create` skips an optional plugin dependency whose own required dependencies cannot be resolved, with a warning, instead of failing the bundle
- `hcli plugin bundle create` skips an optional plugin dependency for a target cell when pip cannot download its Python dependencies for that cell, with a warning, instead of failing the bundle
- `hcli plugin upgrade` fetches the plugin and its dependencies from a plugin bundle that is a configured plugin repository
- `hcli plugin upgrade` does not probe IDA's Python when the repository has no version newer than the installed one
- `hcli plugin bundle create`, `hcli plugin install`, and `hcli plugin upgrade` fail when a plugin dependency name without `@host` matches plugins from more than one host, in any version that can install on the target, instead of treating the dependency as missing
- Plugin dependency installation selects a dependency on the name of an installed plugin only from the host of the installed plugin, and fails when the dependency names a different `@host`, instead of replacing the installed plugin with a plugin from another host
- `hcli plugin bundle create` fails with an error when a local plugin requires a plugin dependency and no plugin repository is available, instead of leaving the dependency out
- `hcli plugin bundle create` fails when a local plugin's `requiresPython` excludes a target cell
- `hcli plugin bundle create` fails with the cause when a local plugin archive is not valid, for example when its entry point file is missing, instead of bundling an archive that the bundle index leaves out
- `hcli plugin bundle create` fails when a plugin dependency pins a version that a local plugin in the bundle does not have, instead of building a bundle that cannot install the dependent plugin
- `hcli plugin bundle create` treats a plugin with the same name and host in two configured repositories as one plugin, instead of reporting it as ambiguous
- Resolve plugin dependencies with an `@host` suffix, or with a name in a different letter case, during `hcli plugin bundle create`
- `hcli plugin bundle create` resolves plugins, plugin dependencies, and Python dependencies separately for each target cell, so a wheelhouse only contains the wheels of the plugins resolved for its cell
- Pass every argument through to the program in `ida python exec` and `ida python run-script`, so `hcli ida python exec -m pip --help` describes pip (#287)
- Update uv.lock for better Python 3.14 support
- Update ida-config.json by default on install
- Explicitly use encoding="utf-8" everywhere and set PYTHONUTF8=1 for subprocesses
- Better algorithm to detect python executable from idat
- Use an isolated minimal IDAUSR for idat-based Python detection, while preserving `idapythonrc.py`, to avoid headless startup crashes from user plugins
- Use ida executable in find_current_ida_platform
- Remove idat invocations for version and platform detection
- Validate all plugin paths before extracting any
- Spell suggested follow-up commands the way hcli was launched (e.g. `uvx ida-hcli ...` under uvx, or the full path when a different `hcli` is first on PATH) instead of a bare `hcli`, which could be missing or an older install (#360)
- Register the `ida://` protocol handler against the running hcli rather than whichever `hcli` comes first on PATH, and via `uvx ida-hcli` when running from uvx's ephemeral cache
- Under uvx, point update notices and `hcli update` at `uvx ida-hcli@latest` instead of `hcli update` / `uv tool upgrade`
- Ask only for plugin settings without a value in `ida-config.json` during `hcli plugin install` (#371)
- Store plugin setting values that equal the default, from install prompts, `--config`, and `hcli plugin config <plugin> setup`, so a stored value can be set back to the default and a reinstall does not ask again (#371)

### Changed
- Move the hcli extension commands from `hcli extension create` and `hcli extension list` to `hcli hcli-extensions create` and `hcli hcli-extensions list`
- Log the Python-relevant environment variables (`VIRTUAL_ENV`, `PYTHONHOME`, `PATH`, ...) passed to `idat` at debug level

## [0.15.13] - 2026-01-27

### Fixed
- Path traversal vulnerability in ZIP extraction (SUPPORT-7539)
- Plugin path normalization on Windows in settings
- Detect current plugin by code path in settings

### Changed
- Migrate parse_plugin_version from deprecated `partial=True` to `Version.coerce()`

## [0.15.12] - 2026-01-25

### Fixed
- Fix crash when sorting plugin versions
- Fix empty repo detection during sync

## [0.15.11] - 2026-01-22

### Fixed
- Fix 403 error when downloading licenses with presigned S3 URLs

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
