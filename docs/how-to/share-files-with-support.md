# How to Share Files with Hex-Rays Support

This guide shows you how to securely upload and share files with Hex-Rays support team when reporting bugs or requesting assistance.

## Problem Statement

You've encountered a bug or crash in IDA Pro and need to share files (IDB files, crash dumps, screenshots) with Hex-Rays support to help them reproduce and fix the issue.

## Prerequisites

- HCLI installed (see [Installation](../getting-started/installation.md))
- Valid authentication (see [Authentication](../getting-started/authentication.md))
- Files to share (IDB files, logs, crash dumps, etc.)

## Quick Start

For the impatient, here's the essential workflow:

```bash
# 1. Upload your file
hcli share put crash_sample.idb --acl authenticated

# 2. Copy the share code (e.g., "abc123")
# 3. Email support@hex-rays.com with the code
# 4. Delete when resolved
hcli share delete abc123
```

## Step-by-Step Guide

### Step 1: Authenticate with HCLI

Verify you're logged in:

```bash
hcli whoami
```

Expected output:

```
You are logged in as user@example.com using an API key from HCLI_API_KEY environment variable
```

If not authenticated, see [Authentication](../getting-started/authentication.md).

### Step 2: Prepare Your Files

Gather the files needed to reproduce the issue:

**For Crashes or Bugs:**

- IDB database files
- The original binary (if possible)
- Log files from `%APPDATA%\Hex-Rays\IDA Pro\` (Windows) or `~/.idapro/` (Linux/macOS)
- Screenshots showing the issue
- Any relevant crash dumps

**Best Practices:**

- Include only files necessary to reproduce the issue
- Compress large files before uploading (optional, but recommended)
- Remove sensitive data if possible
- Note the IDA Pro version and platform in your support email

### Step 3: Upload Files

Upload each file using the `share put` command:

```bash
hcli share put crash_sample.idb
```

You'll be prompted to choose a visibility level:

```
? Pick a visibility
  [private] Just for me
  [domain] Anyone from my domain
» [authenticated] Anyone authenticated with the link
```

**For support requests, choose `authenticated`**. This allows Hex-Rays support staff to access the file.

### Step 4: Choose the Right Visibility Level

Understanding access control levels:

| Level          | Who Can Access                              | Use Case                           |
| -------------- | ------------------------------------------- | ---------------------------------- |
| **private**    | Only you                                    | Personal backups, testing          |
| **domain**     | Anyone with your email domain (@company.com) | Internal team collaboration        |
| **authenticated** | Anyone with the link and valid Hex-Rays authentication | **Sharing with Hex-Rays support** |

For support requests, always use **authenticated**:

```bash
hcli share put crash_sample.idb --acl authenticated
```

### Step 5: Record the Share Code

After successful upload, HCLI displays the file details:

```
Upload Complete 100% 2.4/2.4 MB 5.2 MB/s 0:00:00
✓ File uploaded successfully!
Share Code: efja98
Share URL: https://my.hex-rays.com/share/efja98
Download URL: https://api.eu.hex-rays.com/api/assets/s/efja98
```

**Copy the Share Code** (e.g., `efja98`) - you'll need this for your support email.

### Step 6: Verify the Upload

List your shared files to confirm:

```bash
hcli share list --no-interactive
```

Output:

```
┏━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━┓
┃ Index ┃ Code                 ┃ Version ┃ Size     ┃ Created            ┃ ACL           ┃
┡━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━┩
│ 1     │ efja98               │ 1       │ 2.4 MB   │ 2025-10-24 14:30   │ authenticated │
└───────┴──────────────────────┴─────────┴──────────┴────────────────────┴───────────────┘
```

Verify:

- The file appears in the list
- ACL is `authenticated`
- File size looks correct

### Step 7: Contact Hex-Rays Support

Email support@hex-rays.com with:

**Subject:** `[HCLI Share: efja98] Brief description of issue`

**Body:**

```
Hello,

I've encountered [brief description of the issue] while using IDA Pro.

Shared Files:
- Share Code: efja98 (crash_sample.idb)
- Share Code: xyz789 (crash.log)

Environment:
- IDA Pro Version: 9.2
- Platform: Windows 11 x64
- HCLI Version: [run: hcli --version]

Steps to Reproduce:
1. Open the shared IDB file
2. Navigate to address 0x401000
3. Click "Create Function"
4. IDA crashes

Please let me know if you need any additional information.

Best regards,
Your Name
```

**Important:**

- Include all share codes
- Describe the issue clearly
- Provide steps to reproduce
- Mention your IDA Pro version and platform

### Step 8: Hex-Rays Accesses Your Files

Support staff can download your files using:

```bash
hcli share get efja98
```

They'll see:

```
Downloading crash_sample.idb 100%
✓ File downloaded successfully!
File: crash_sample.idb
Size: 2.4 MB
Saved to: crash_sample.idb
```

No action needed from you - the support team has the necessary authentication.

### Step 9: Clean Up After Resolution

Once the issue is resolved, delete the shared files:

```bash
hcli share delete efja98
```

Confirm deletion:

```
File to delete:
  Name: crash_sample.idb
  Code: efja98
  Size: 2.4 MB

Delete file crash_sample.idb ? [y/n]: y
✓ Deleted: efja98
```

Or delete without confirmation:

```bash
hcli share delete efja98 --force
```

## Advanced Workflows

### Sharing Multiple Files

Upload multiple files at once:

```bash
# Upload each file
hcli share put crash_sample.idb --acl authenticated
hcli share put crash.log --acl authenticated
hcli share put screenshot.png --acl authenticated

# List all to get share codes
hcli share list --no-interactive
```

### Updating a Shared File

If support needs an updated version:

```bash
# Upload new version using existing code
hcli share put crash_sample_v2.idb --code efja98
```

This creates version 2 of the same share code.

### Interactive Management

Use interactive mode to manage multiple files:

```bash
hcli share list
```

Then:

1. Select files using checkboxes (Space to toggle)
2. Choose action: Delete selected files or Download selected files
3. Confirm the action

### Batch Upload

Upload all files in a directory:

```bash
for file in crash_files/*; do
  echo "Uploading: $file"
  hcli share put "$file" --acl authenticated
done
```

## Troubleshooting

## Best Practices

### Do's

1. **Use `authenticated` ACL** for support requests
2. **Include descriptive email** with share codes and reproduction steps
3. **Verify uploads** by listing your shared files
4. **Remove sensitive data** when possible
5. **Compress large files** before uploading

### Don'ts

1. **Don't use `private` ACL** - support won't be able to access
2. **Don't share codes publicly** (forums, GitHub issues)
3. **Don't upload unnecessary files** - focus on reproduction
4. **Don't email large files** - use `hcli share` instead

## Reference Documentation

For more detailed information:

- [File Sharing User Guide](../user-guide/file-sharing.md) - Complete reference
- [Authentication](../getting-started/authentication.md) - How to log in
- [Environment Variables](../reference/environment-variables.md) - Configuration options

## Security Notes

- All uploads use HTTPS encryption
- Access control is enforced at the API level
- Files are associated with your user account
- Share codes are unique and unpredictable
- Only authenticated users can access `authenticated` files
- You can delete your files at any time

## Support Contact Information

- **Email:** support@hex-rays.com
- **Response Time:** Typically 1-2 business days
- **Include:** Share codes, IDA version, platform, and reproduction steps
