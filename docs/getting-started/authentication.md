# Authentication

## Overview

hcli supports two authentication methods:

- OAuth (interactive login)
- API Keys (recommended for automation)

## API Key Authentication

### Creating an API Key

```bash
hcli auth key create --name "hcli"
```

### Installing an API Key

```bash
hcli auth key install <api-key>
```

### Managing API Keys

List your API keys:
```bash
hcli auth key list
```

Revoke an API key:
```bash
hcli auth key revoke <key-id>
```

## OAuth Authentication

### Interactive Login

```bash
hcli login
```

This will open your browser to complete the authentication flow, or you can use a one time password email flow. 

### Logout

```bash
hcli logout
```

## Verify Authentication

Check your authentication status:
```bash
hcli whoami
```

## Environment Variables

You can also set your API key via environment variable:
```bash
export HCLI_API_KEY=your-api-key-here
```

## Next Steps

- [Quick Start](quick-start.md) - Start using the CLI