# Authentication

## Overview

HCLI supports two authentication methods:

- OAuth (interactive login)
- API Keys (recommended for automation)

Use the OAuth method for interactive systems, like your primary workstation.
HCLI will open a browser window so that you can sign into my.hex-rays.com and link your account.
You can subsequently link other accounts and switch between them on-demand.

The API key method is best for automated environments, such as running HCLI in a Docker container or CI/CD environment.
You'll generate an API key and keep it secure, passing it to HCLI via an environment variable (`HCLI_API_KEY`).

## OAuth Authentication

```bash
hcli login
```

This will open your browser to complete the authentication flow, or you can use a one time password email flow. 

```bash
hcli logout
```

## API Key Authentication

Creating an API Key:

```bash
hcli auth key create --name "hcli-test-key"
The key will be displayed only once, so make sure to save it in a secure place.
? Do you want to create a new API key hcli-test-key? (Y/n) Yes
API key created: hrp-1-fdsafdsafdsafdafdsafdsafdfasdssda
? Do you want to use this key for hcli? (Y/n) No
```

Install an existing key (or use `HCLI_API_KEY` environment variable):

```bash
hcli auth key install <api-key, hrp-1-fdsafdasfdasfdsafda...>
```

List your API keys:

```bash
hcli auth key list
┏━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━┳━━━━━━━━━━┓
┃ Name                 ┃ Created     ┃ Last Used      ┃ Requests ┃
┡━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━╇━━━━━━━━━━┩
│ hcli-test-key        │ Oct 24 2025 │ never          │        0 │
│ zydis-gh-actions     │ Oct 09 2025 │ 14 days ago    │       45 │
│ binexport-gh-actions │ Oct 06 2025 │ 16 days ago    │       49 │
│ bindiff-gh-actions   │ Oct 02 2025 │ 16 days ago    │      180 │
│ goomba-gh-actions    │ Oct 02 2025 │ 21 days ago    │       56 │
│ zydisinfo-gh-actions │ Aug 29 2025 │ just now       │      316 │
│ hcli-gh-actions      │ Aug 25 2025 │ 11 minutes ago │    44200 │
└──────────────────────┴─────────────┴────────────────┴──────────┘
```

Revoke an API key:

```bash
hcli auth key revoke
? Select API key to revoke: (Use arrow keys)
 » hcli-test-key (Created: 2025-10-24, Requests: 0)
   zydis-gh-actions (Created: 2025-10-09, Requests: 45)
   binexport-gh-actions (Created: 2025-10-06, Requests: 49)
   bindiff-gh-actions (Created: 2025-10-02, Requests: 180)
   goomba-gh-actions (Created: 2025-10-02, Requests: 56)
   zydisinfo-gh-actions (Created: 2025-08-29, Requests: 317)
   hcli-gh-actions (Created: 2025-08-25, Requests: 44200)
? Do you want to revoke the key named 'hcli-test-key'? Yes
Revoking API key 'hcli-test-key'...
API key 'hcli-test-key' has been revoked
```

## Verify Authentication

Check your authentication status:
```bash
hcli whoami
You are logged in as user@example.com using an API key from HCLI_API_KEY environment variable
```

## Environment Variables

You can also set your API key via environment variable:
```bash
export HCLI_API_KEY=hrp-1-fdsafdsafdsafdsafdsa....
```

## Next Steps

- [Quick Start](quick-start.md) - Start using the CLI
