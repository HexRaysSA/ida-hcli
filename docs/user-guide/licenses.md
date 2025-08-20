# License Management

## Overview

hcli helps you manage your IDA Pro licenses, including viewing, installing, and configuring license files.

## Viewing Licenses

List all available licenses:
```bash
hcli license list
```

Get detailed information about a license:
```bash
hcli license get <license-id>
```

## Installing Licenses

### Automatic Installation

Install a license automatically:
```bash
hcli license install
```

This will:
1. Download your license file
2. Install it to the appropriate location
3. Configure IDA Pro to use the license

### Manual Installation

If you have a license file locally:
```bash
hcli license install --file /path/to/license.lic
```

## License Locations

Licenses are installed to:
- **Windows**: `%APPDATA%\Hex-Rays\IDA Pro\`
- **macOS**: `~/Library/Application Support/Hex-Rays/IDA Pro/`
- **Linux**: `~/.idapro/`

## License Types

### Node-Locked Licenses
- Tied to a specific machine
- Automatically activated during installation

### Floating Licenses
- Require connection to a license server
- Configure server details during installation

### Evaluation Licenses
- Time-limited licenses for evaluation
- Automatically expire after the evaluation period

## Troubleshooting

### License Not Found

1. Verify your authentication:
   ```bash
   hcli whoami
   ```

2. Check your license entitlements:
   ```bash
   hcli license list
   ```

### Installation Issues

- Ensure IDA Pro is not running during license installation
- Check file permissions in the IDA Pro directory
- Verify network connectivity for floating licenses

### License Server Issues

For floating licenses:
- Verify license server connectivity
- Check firewall settings
- Confirm server port accessibility

