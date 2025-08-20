# Quick Start

## First Steps

1. **Install ida-hcli** (see [Installation](installation.md))
2. **Authenticate** (see [Authentication](authentication.md))
3. **Verify your setup**:
   ```bash
   hcli whoami
   ```

## Common Commands

### License Management

View your licenses:
```bash
hcli license list
```

Install a license:
```bash
hcli license install
```

### File Sharing

Share a file:
```bash
hcli share put myfile.idb
```

List shared files:
```bash
hcli share list
```

Download a shared file:
```bash
hcli share get <file-id>
```

## Help and Documentation

Get help for any command:
```bash
hcli --help
hcli license --help
hcli license install --help
```

## Next Steps

- [License Management](../user-guide/licenses.md) - Managing your IDA licenses
- [File Sharing](../user-guide/file-sharing.md) - Share and collaborate on files