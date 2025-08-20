# File Sharing

## Overview

hcli provides secure file sharing capabilities, allowing you to share IDA databases, analysis results, and other files with our support team.

## Sharing Files

### Upload a File

Share a file:
```bash
hcli share put <file-path>
```

Share with custom metadata:
```bash
hcli share put <file-path>
```

### Share Options

- `--name`: Custom name for the shared file
- `--description`: Description of the file contents
- `--expires`: Expiration date (e.g., "2024-12-31")

## Managing Shared Files

### List Your Shared Files

```bash
hcli share list
```

### Get File Information

```bash
hcli share get <file-id> --info
```

### Download a Shared File

```bash
hcli share get <file-id>
```

Download to specific location:
```bash
hcli share get <file-id> --output /path/to/save/
```

### Delete a Shared File

```bash
hcli share delete <file-id>
```

## File Types

### Supported Formats

- **IDA Databases**: `.idb`, `.i64`
- **Analysis Results**: `.txt`, `.json`, `.xml`
- **Binary Files**: `.exe`, `.bin`, `.dll`
- **Archives**: `.zip`, `.tar.gz`
- **Documents**: `.pdf`, `.doc`, `.md`

## Security and Privacy

### Access Control

- **Private**: Only you can access (default)
- **Shared**: Accessible via link
- **Public**: Discoverable in public listings

### Encryption

- All files encrypted at rest
- Secure transmission via HTTPS
- Access logs maintained for security

### Expiration

- Set automatic expiration dates
- Files automatically deleted after expiration
- Email notifications before expiration

## Collaboration Features

### Team Sharing

Share with specific team members:
```bash
hcli share put <file> --team <team-id>
```

### Comments and Annotations

Add comments to shared files:
```bash
hcli share comment <file-id> "Analysis looks good"
```

### Version Control

- Automatic versioning for updated files
- Download specific versions
- Compare versions

## Best Practices

### Naming Conventions

- Use descriptive names for shared files
- Include version numbers for iterative analysis
- Tag files with relevant keywords

### Security Guidelines

- Don't share sensitive or confidential data
- Use expiration dates for temporary shares
- Regularly review and clean up old shares

### Organization

- Use consistent naming conventions
- Group related files together
- Document analysis methodology in descriptions

## Troubleshooting

### Upload Issues

- Check file size limits
- Verify file format is supported
- Ensure stable internet connection

### Download Problems

- Verify file ID is correct
- Check available disk space
- Confirm file hasn't expired

### Access Denied

- Verify authentication status
- Check file sharing permissions
- Contact file owner for access

