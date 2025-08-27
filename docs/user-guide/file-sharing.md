# File Sharing

## Overview

hcli provides secure file sharing capabilities, allowing you to upload and share files with Hex-Rays support team or within your organization. The shared files are accessible via unique shortcodes and can be managed through various commands.

## Sharing Files

### Upload a File

Share a file and get a unique shortcode:
```bash
hcli share put <file-path>
```

### Upload Options

- `-a, --acl`: Set access control level (private, authenticated, domain)
  - **private**: Only you can access the file
  - **authenticated**: Anyone authenticated with the link can access
  - **domain**: Anyone from your email domain can access
- `-c, --code`: Upload a new version for an existing shortcode
- `-f, --force`: Force upload a new version for an existing shortcode

Examples:
```bash
# Upload with specific ACL
hcli share put myfile.idb --acl private

# Upload new version for existing code
hcli share put updated.idb --code ABC123

# Force upload (overwrites if exists)
hcli share put analysis.txt --force
```

### Interactive ACL Selection

When no ACL is specified, hcli will interactively prompt you to choose:
1. **[private] Just for me** - Only you can access
2. **[domain] Anyone from my domain** - Anyone with your email domain
3. **[authenticated] Anyone authenticated with the link** - Anyone with valid authentication

### Output

After successful upload, you'll receive:
- **Share Code**: Unique shortcode for the file
- **Share URL**: Web URL to access the file
- **Download URL**: Direct download link

## Downloading Shared Files

### Download a File

Download a shared file using its shortcode:
```bash
hcli share get <SHORTCODE>
```

### Download Options

- `-o, --output-dir`: Specify output directory (default: current directory)
- `-O, --output-file`: Specify exact output file path
- `-f, --force`: Overwrite existing files without confirmation

Examples:
```bash
# Download to current directory
hcli share get ABC123

# Download to specific directory
hcli share get ABC123 --output-dir /downloads/

# Download with custom filename
hcli share get ABC123 --output-file /path/to/renamed-file.idb

# Force overwrite if exists
hcli share get ABC123 --force
```

## Managing Shared Files

### List Your Shared Files

View all your shared files in a table or interactive mode:
```bash
hcli share list
```

### List Options

- `--limit`: Maximum number of files to display (default: 100)
- `--offset`: Offset for pagination (default: 0)
- `--interactive/--no-interactive`: Enable/disable interactive mode (default: enabled)

#### Table View (Non-Interactive)

```bash
hcli share list --no-interactive
```

Displays a table with:
- Index number
- Shortcode
- Filename
- Version
- File size
- Creation date
- ACL type

#### Interactive Mode

```bash
hcli share list
```

In interactive mode, you can:
1. Select multiple files using checkboxes
2. Choose an action:
   - **Delete selected files**: Remove files with confirmation
   - **Download selected files**: Batch download to a directory

### Delete Shared Files

Delete a shared file by its shortcode:
```bash
hcli share delete <SHORTCODE>
```

### Delete Options

- `-f, --force`: Skip confirmation prompt

Examples:
```bash
# Delete with confirmation
hcli share delete ABC123

# Delete without confirmation
hcli share delete ABC123 --force
```

The delete command will show:
- File name
- Shortcode
- File size

## File Versioning

The share system supports file versioning:
- Each file has a version number (starting from 1)
- Use `--code` option with `put` to upload new versions
- Use `--force` to overwrite without versioning
- Version numbers are displayed in list view

## Security and Access Control

### Access Control Levels (ACL)

1. **Private**: Only the uploader can access the file
2. **Domain**: Anyone with the same email domain as the uploader
3. **Authenticated**: Anyone with valid authentication credentials

### Security Features

- All files are transmitted securely over HTTPS
- Authentication required for all operations
- Access control enforced at the API level
- Files are associated with your user account

## File Size Display

File sizes are automatically formatted for readability:
- B (Bytes)
- KB (Kilobytes)
- MB (Megabytes)
- GB (Gigabytes)
- TB (Terabytes)

## Best Practices

### File Management

1. **Use descriptive filenames**: Makes files easier to identify in lists
2. **Regular cleanup**: Periodically delete old files you no longer need
3. **Version control**: Use `--code` to maintain versions of the same file
4. **Choose appropriate ACL**: Consider who needs access before uploading

### Security Guidelines

1. **Review ACL settings**: Always verify the access level before sharing
2. **Domain sharing**: Be aware that domain-level sharing includes all users from your organization
3. **Private by default**: When in doubt, use private ACL
4. **Delete sensitive files**: Remove files containing sensitive data after use

## Common Use Cases

### Sharing with Hex-Rays Support

```bash
# Upload IDB for support analysis
hcli share put crash_analysis.idb --acl authenticated

# Share the code with support team
# They can download using: hcli share get <YOUR-CODE>
```

### Team Collaboration

```bash
# Share within your organization
hcli share put project.i64 --acl domain

# Team members from your domain can access
hcli share get <SHORTCODE>
```

### Version Management

```bash
# Initial upload
hcli share put analysis_v1.txt
# Returns code: ABC123

# Upload new version
hcli share put analysis_v2.txt --code ABC123
# Same code, new version
```

## Troubleshooting

### Upload Issues

- **"File not found"**: Verify the file path exists
- **"Path is not a file"**: Ensure you're uploading a file, not a directory
- **"--force and --code cannot be used together"**: Choose one versioning method

### Download Problems

- **"File with shortcode not found"**: Verify the shortcode is correct
- **"File already exists"**: Use `--force` to overwrite or choose different output
- **"No download URL available"**: File may have been deleted or access denied

### Access Issues

- **Authentication required**: Run `hcli auth login` first
- **Access denied**: Check if you have permission for the file's ACL level
- **User not found**: Ensure you're properly authenticated
