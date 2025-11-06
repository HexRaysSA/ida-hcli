# How to Test Your Plugin Before Publishing

This guide shows you how to thoroughly test your IDA plugin before publishing it to the plugin repository, ensuring it meets quality standards and works correctly across different environments.

## Problem Statement

You've developed an IDA Pro plugin and want to test it comprehensively before releasing it to ensure:

- The plugin metadata is valid
- It installs correctly
- It works across supported IDA versions
- Python dependencies are properly declared
- There are no common packaging errors

## Prerequisites

- HCLI installed (see [Installation](../getting-started/installation.md))
- Your plugin with `ida-plugin.json` file
- Valid IDA Pro installation (for runtime testing)
- Authentication configured (for dependency installation)

## Quick Validation Checklist

Before deep testing, ensure these basics:

- [ ] `ida-plugin.json` exists in plugin root
- [ ] Plugin version follows semantic versioning (e.g., `1.0.0`)
- [ ] Repository URL is correct
- [ ] Entry point file exists
- [ ] No trailing commas in JSON

## Step-by-Step Testing Guide

### Step 1: Validate Plugin Metadata with Lint

The first step is to validate your plugin structure and metadata:

```bash
hcli plugin lint /path/to/your-plugin
```

**For a directory:**

```bash
cd /path/to/your-plugin
hcli plugin lint .
```

**For a ZIP archive:**

```bash
hcli plugin lint your-plugin-v1.0.0.zip
```

**For a GitHub release:**

```bash
hcli plugin lint https://github.com/username/plugin/releases/download/v1.0.0/plugin.zip
```

### Step 2: Understand Lint Output

**Success output:**

```
✓ Plugin validation successful!

Plugin: my-awesome-plugin
Version: 1.0.0
Entry Point: plugin_entry.py
IDA Versions: 9.1, 9.2
Platforms: windows-x86_64, linux-x86_64, macos-aarch64

Warnings:
  - Consider adding keywords to improve discoverability
  - No description provided

Suggestions:
  - Add a logo (logoPath) for better visibility in the plugin browser
```

**Error output:**

```
✗ Plugin validation failed!

Errors:
  - Missing required field: plugin.version
  - Invalid JSON: Unexpected token '}' at line 15
  - Entry point file 'plugin.py' not found
  - Python dependency 'requets' not found on PyPI (did you mean 'requests'?)

Fix these errors before publishing.
```

### Step 3: Fix Common Validation Errors

#### Error: "Invalid JSON syntax"

**Cause:** Trailing commas or syntax errors in `ida-plugin.json`.

**Solution:** Use a JSON validator or `jq`:

```bash
cat ida-plugin.json | jq .
```

Fix trailing commas:

```json
{
  "plugin": {
    "name": "my-plugin",
    "version": "1.0.0"  // ← Remove comma if this is the last field
  }
}
```

#### Error: "Missing required field: plugin.version"

**Cause:** Version field is missing or empty.

**Solution:** Add semantic version:

```json
{
  "plugin": {
    "version": "1.0.0"
  }
}
```

#### Error: "Entry point file not found"

**Cause:** File specified in `entryPoint` doesn't exist.

**Solution:** Verify the file exists and path is correct. This path is relative to `ida-plugin.json`:

```bash
# Check entry point
ls -l plugin_entry.py

# Update ida-plugin.json
{
  "plugin": {
    "entryPoint": "plugin_entry.py"  // Must match actual filename
  }
}
```

#### Error: "Python dependency not found on PyPI"

**Cause:** Typo in dependency name or package doesn't exist.

**Solution:** Verify package exists:

```bash
pip search requests  # or check pypi.org
```

Fix in `ida-plugin.json`:

```json
{
  "plugin": {
    "pythonDependencies": [
      "requests>=2.28.0",  // ← Fixed typo from "requets"
      "pydantic>=2.0"
    ]
  }
}
```

#### Error: "Invalid version specifier"

**Cause:** Version doesn't follow semantic versioning.

**Solution:** Use format `MAJOR.MINOR.PATCH`:

```json
// ✗ Wrong
{"version": "1.0"}
{"version": "v1.0.0"}
{"version": "1.01.0"}
{"version": "1.0.0-beta"}  // Pre-release tags not fully supported yet

// ✓ Correct
{"version": "1.0.0"}
{"version": "2.1.3"}
```

### Step 4: Test Local Installation

Install your plugin from the local directory:

```bash
# Install from directory
hcli plugin install /path/to/your-plugin

# Or from ZIP
hcli plugin install ./your-plugin-v1.0.0.zip
```

Expected output:

```
Installing plugin: my-awesome-plugin==1.0.0
Installing Python dependencies: requests>=2.28.0, pydantic>=2.0
✓ Installed plugin: my-awesome-plugin==1.0.0
```

### Step 5: Verify Installation

Check that the plugin is installed:

```bash
hcli plugin status
```

Output should include your plugin:

```
✓ my-awesome-plugin    1.0.0    /path/to/.idapro/plugins/my-awesome-plugin/
  requests             2.31.0
  pydantic             2.5.0
```

Verify plugin files were copied:

```bash
# macOS/Linux
ls ~/.idapro/plugins/my-awesome-plugin/

# Windows
dir %APPDATA%\.idapro\plugins\my-awesome-plugin\
```

### Step 6: Test in IDA Pro

Launch IDA Pro and verify:

1. **Plugin loads without errors:**
   - Check IDA console output for Python errors
   - Look for your plugin in Edit → Plugins menu

2. **Plugin functionality works:**
   - Execute main plugin features
   - Test edge cases
   - Verify hotkeys (if any)

3. **Check for errors:**
   ```python
   # In IDA Python console
   import ida_loader
   ida_loader.load_and_run_plugin("my-awesome-plugin", 0)
   ```

### Step 7: Test Across IDA Versions

If your plugin supports multiple IDA versions, test each one:

```bash
# Set specific IDA installation
export HCLI_CURRENT_IDA_INSTALL_DIR="/Applications/IDA Professional 9.1.app"
hcli plugin install /path/to/your-plugin

# Test in IDA 9.1
/Applications/IDA\ Professional\ 9.1.app/Contents/MacOS/ida64

# Repeat for IDA 9.2
export HCLI_CURRENT_IDA_INSTALL_DIR="/Applications/IDA Professional 9.2.app"
hcli plugin install /path/to/your-plugin --force

# Test in IDA 9.2
/Applications/IDA\ Professional\ 9.2.app/Contents/MacOS/ida64
```

**Verify:**

- No API compatibility issues
- All features work as expected
- No deprecation warnings

### Step 8: Test Plugin Configuration (if applicable)

If your plugin uses settings:

```bash
# List plugin settings
hcli plugin config list my-awesome-plugin

# Set a configuration value
hcli plugin config set my-awesome-plugin api_key "test-key-123"

# Get the value
hcli plugin config get my-awesome-plugin api_key

# Test in plugin
# Your plugin should read: ida_settings.get_current_plugin_setting("api_key")
```

### Step 9: Test Uninstallation

Verify clean removal:

```bash
hcli plugin uninstall my-awesome-plugin
```

Check that files are removed:

```bash
ls ~/.idapro/plugins/my-awesome-plugin/  # Should not exist
```

Re-install to continue testing:

```bash
hcli plugin install /path/to/your-plugin
```

## Pre-Publishing Checklist

Before creating a GitHub release:

### Metadata Completeness

- [ ] `version` follows semantic versioning
- [ ] `description` is clear and concise
- [ ] `keywords` added for discoverability
- [ ] `license` specified
- [ ] `authors` and/or `maintainers` with email
- [ ] `urls.repository` points to correct GitHub repo
- [ ] `idaVersions` lists all supported versions
- [ ] `platforms` specified (or omit for all platforms)
- [ ] `logoPath` included (optional but recommended)

### Testing Complete

- [ ] `hcli plugin lint` passes without errors
- [ ] Plugin installs from ZIP archive
- [ ] Plugin loads in IDA Pro without errors
- [ ] Core functionality works as expected
- [ ] Tested on all supported IDA versions
- [ ] Python dependencies install correctly
- [ ] Settings/configuration works (if applicable)
- [ ] Plugin uninstalls cleanly

### Documentation

- [ ] README.md with usage instructions
- [ ] CHANGELOG.md with version history
- [ ] LICENSE file included
- [ ] Example usage provided
- [ ] Known issues documented

### Repository Setup

- [ ] GitHub repository is public
- [ ] `ida-plugin.json` is present
- [ ] Release tags follow semantic versioning (e.g., `v1.0.0`)
- [ ] ZIP archive attached to release (for non-Python plugins)
- [ ] Release notes describe changes

## Automated Testing

## Common Testing Pitfalls

### 1. Not Testing on Clean IDA Installation

**Problem:** Plugin works on your system but fails for users.

**Solution:** Test with fresh IDA installation:

```bash
# Backup existing plugins
mv ~/.idapro/plugins ~/.idapro/plugins.bak

# Test plugin installation
hcli plugin install /path/to/plugin

# Restore
mv ~/.idapro/plugins.bak ~/.idapro/plugins
```

### 2. Hardcoded Paths

**Problem:** Plugin uses absolute paths that don't exist on other systems.

**Solution:** Use IDA SDK functions:

```python
import ida_diskio
import idaapi

# ✗ Wrong
config_path = "/Users/me/.plugin-config"

# ✓ Correct
config_path = os.path.join(ida_diskio.get_user_idadir(), "plugin-config")
```

### 3. Missing Python Dependency Versions

**Problem:** Plugin works with latest dependency but breaks with older versions.

**Solution:** Specify minimum versions:

```json
{
  "pythonDependencies": [
    "requests>=2.28.0",  // Specify minimum version
    "pydantic>=2.0,<3.0" // Or version range
  ]
}
```

Prefer not to pin to specific versions (`==2.28.0`) because its difficult for many plugins to agree on precisely the same version.

## Reference Documentation

For more detailed information:

- [Packaging Your Existing Plugin](../reference/packaging-your-existing-plugin.md)
- [Plugin Packaging and Format](../reference/plugin-packaging-and-format.md)
- [Plugin Manager User Guide](../user-guide/plugin-manager.md)
- [Plugin Repository Architecture](../reference/plugin-repository-architecture.md)

## Getting Help

If you encounter issues:

1. **Check the linter output:** `hcli plugin lint` provides detailed error messages
2. **Review example plugins:** https://github.com/HexRaysSA/plugin-repository
3. **Open an issue:** https://github.com/HexRaysSA/ida-hcli/issues
4. **Contact Hex-Rays:** support@hex-rays.com

## Publishing Your Plugin

Once all tests pass:

1. Create a GitHub release with a semantic version tag (e.g., `v1.0.0`)
2. Attach ZIP archive (if needed for native plugins)
3. Wait for the daily indexer run
4. Verify plugin appears: `hcli plugin search your-plugin`

The plugin repository automatically discovers and indexes plugins with valid `ida-plugin.json` files!
