# CLI Commands Reference

This page provides comprehensive documentation for all available HCLI commands. You can also get a quick overview of all commands using:

```bash
hcli commands
```

## Overview

HCLI provides commands organized into the following categories:

- **Authentication**: Manage API keys and user credentials
- **Downloads**: Download IDA Pro binaries, SDKs, and utilities  
- **Licenses**: Manage and install IDA Pro licenses
- **Installations**: Install and manage IDA Pro installations
- **File Sharing**: Upload and download shared files
- **Extensions**: Manage HCLI extensions
- **Utilities**: Update checking, user information, and more

## Global Options

All commands support these global options:

- `--quiet, -q`: Run without prompting the user
- `--auth, -a`: Force authentication type (interactive|key)
- `--auth-credentials, -s`: Force specific credentials by name
- `--disable-updates`: Disable automatic update checking
- `--help`: Show help information

## Commands

### Authentication Commands

#### `hcli auth`
Manage HCLI API keys and authentication.

**Subcommands:**

##### `hcli auth list`
List all stored credentials.

```bash
hcli auth list
```

##### `hcli auth default`
Set or show the default credentials.

```bash
hcli auth default
hcli auth default [CREDENTIAL_NAME]
```

##### `hcli auth switch`
Switch the default credentials.

```bash
hcli auth switch
```

##### `hcli auth key`
API key management commands.

**Subcommands:**

###### `hcli auth key create`
Create a new API key.

```bash
hcli auth key create
```

###### `hcli auth key install`
Install an API key as new credentials.

```bash
hcli auth key install
```

###### `hcli auth key list`
List all API keys.

```bash
hcli auth key list
```

###### `hcli auth key revoke`
Revoke an API key.

```bash
hcli auth key revoke
```

### User Management

#### `hcli login`
Log in to the Hex-Rays portal and create new credentials.

```bash
hcli login [OPTIONS]
```

**Options:**
- `--force, -f`: Force account selection
- `--name, -n TEXT`: Custom name for the credentials

#### `hcli logout`
Log out and remove stored credentials.

```bash
hcli logout
```

#### `hcli whoami`
Display the currently logged-in user.

```bash
hcli whoami
```

### Download Commands

#### `hcli download`
Download IDA binaries, SDKs, and utilities.

```bash
hcli download [OPTIONS] [KEY]
```

**Arguments:**
- `KEY`: The asset key for direct download (e.g., `release/9.1/ida-pro/ida-pro_91_x64linux.run`) (optional)

**Options:**
- `--force, -f`: Skip cache
- `--mode TEXT`: One of `interactive` or `direct`
- `--output-dir TEXT`: Output path
- `--pattern TEXT`: Pattern to search for assets

**Examples:**

Interactive mode (default):
```bash
hcli download
```

Direct mode with pattern:
```bash
hcli download --mode direct --pattern "ida-pro_91"
```

Direct download by key:
```bash
hcli download release/9.1/ida-pro/ida-pro_91_x64linux.run
```

### License Management

#### `hcli license`
Manage IDA licenses.

**Subcommands:**

##### `hcli license list`
List available licenses with rich formatting.

```bash
hcli license list
```

##### `hcli license get`
Download license files with optional filtering.

```bash
hcli license get [OPTIONS]
```

**Options:**
- `--id, -i TEXT`: License ID (e.g., `48-307B-71D4-46`)
- `--plan, -p [subscription|legacy]`: Plan type: subscription or legacy
- `--type, -t TEXT`: License type (e.g., `IDAPRO`, `IDAHOME`, `LICENSE_SERVER`)
- `--all, -a`: Get all matching licenses
- `--output-dir TEXT`: Output directory for license files

**Examples:**

List all licenses:
```bash
hcli license list
```

Download a specific license:
```bash
hcli license get --id 48-307B-71D4-46
```

Download all subscription licenses:
```bash
hcli license get --plan subscription --all
```

##### `hcli license install`
Install a license file to an IDA Pro installation directory.

```bash
hcli license install
```

### IDA Installation Management

#### `hcli ida`
Manage IDA installations.

**Subcommands:**

##### `hcli ida install`
Install IDA unattended.

```bash
hcli ida install [OPTIONS] [INSTALLER]
```

**Arguments:**
- `INSTALLER`: Path to IDA installer file (optional)

**Options:**
- `--set-default`: Mark this IDA installation as the default
- `--accept-eula, -a`: Accept EULA
- `--install-dir, -i TEXT`: Install directory
- `--license-id, -l TEXT`: License ID (e.g., `48-307B-71D4-46`)
- `--download-id, -d TEXT`: Installer slug

**Installation Paths:**
- Windows: `{install_dir}/ida`
- Linux: `{install_dir}/ida`
- macOS: `{install_dir}/Contents/MacOS/ida`

**Examples:**

Install from local file:
```bash
hcli ida install --accept-eula --install-dir /opt/ida installer.run
```

Install with auto-download and license:
```bash
hcli ida install --accept-eula --download-id ida-pro_91 --license-id 48-307B-71D4-46
```

### File Sharing

#### `hcli share`
Share files with Hex-Rays.

**Subcommands:**

##### `hcli share list`
List and manage your shared files.

```bash
hcli share list
```

##### `hcli share put`
Upload a shared file.

```bash
hcli share put [OPTIONS] PATH
```

**Arguments:**
- `PATH`: Path to file to upload

**Options:**
- `--acl, -a [private|authenticated|domain]`: Access control level
- `--code, -c TEXT`: Upload a new version for an existing code
- `--force, -f`: Upload a new version for an existing code

**Examples:**

Upload a private file:
```bash
hcli share put --acl private myfile.idb
```

Update an existing shared file:
```bash
hcli share put --code ABC123 --force myfile.idb
```

##### `hcli share get`
Download a shared file using its shortcode.

```bash
hcli share get [OPTIONS] SHORTCODE
```

**Arguments:**
- `SHORTCODE`: The shortcode of the file to download

**Options:**
- `--output-dir, -o PATH`: Output directory (default: current directory)
- `--output-file, -O PATH`: Output file path (conflicts with --output-dir)
- `--force, -f`: Overwrite existing files

**Examples:**

Download to current directory:
```bash
hcli share get ABC123
```

Download to specific location:
```bash
hcli share get ABC123 --output-dir /tmp/downloads
```

##### `hcli share delete`
Delete shared file by code.

```bash
hcli share delete
```

### Extension Management

#### `hcli extension`
Manage HCLI extensions.

**Subcommands:**

##### `hcli extension list`
List HCLI extensions.

```bash
hcli extension list
```

##### `hcli extension create`
Create an HCLI extension.

```bash
hcli extension create
```

### Utility Commands

#### `hcli commands`
List all available command combinations.

```bash
hcli commands
```

This command displays a comprehensive table of all available commands with descriptions.

#### `hcli update`
Check for HCLI updates.

```bash
hcli update [OPTIONS]
```

**Options:**
- `--force, -f`: Force update
- `--check-only`: Only check for updates, do not suggest installation
- `--auto-install`: Automatically install update if available (for binary version only)
- `--include-prereleases`: Include pre-release versions when checking GitHub (for binary version only)

**Examples:**

Check for updates:
```bash
hcli update --check-only
```

Force update:
```bash
hcli update --force
```

## Common Workflows

### Getting Started
1. **Login to Hex-Rays portal:**
   ```bash
   hcli login
   ```

2. **Check your authentication:**
   ```bash
   hcli whoami
   ```

3. **List available licenses:**
   ```bash
   hcli license list
   ```

### Download and Install IDA
1. **Download IDA installer:**
   ```bash
   hcli download --mode interactive
   ```

2. **Install IDA with license:**
   ```bash
   hcli ida install --accept-eula --install-dir /opt/ida --license-id YOUR-LICENSE-ID installer.run
   ```

### Share Files
1. **Upload a file:**
   ```bash
   hcli share put --acl authenticated myanalysis.idb
   ```

2. **List your shared files:**
   ```bash
   hcli share list
   ```

3. **Download someone's shared file:**
   ```bash
   hcli share get ABC123
   ```

## Getting Help

For any command, you can get detailed help using the `--help` flag:

```bash
hcli --help                    # Main help
hcli auth --help               # Auth commands help
hcli download --help           # Download command help
hcli license get --help        # Specific subcommand help
```