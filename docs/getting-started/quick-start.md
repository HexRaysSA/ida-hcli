# Quick Start

## First Steps

1. **Install HCLI** (see [Installation](installation.md))
2. **Authenticate** (see [Authentication](authentication.md))
3. **Verify your setup**:
   ```bash
   hcli whoami
   You are logged in as wballenthin@hex-rays.com using an API key from HCLI_API_KEY environment variable
   ```


## Command Overview (abbreviated)

Here are the core operations supported by HCLI:

```bash
$ hcli

╭─ Commands ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╮
│ download              Download IDA binaries, SDKs, and utilities.                                                                          │
│ ida                   Manage IDA installations.                                                                                            │
│ license               Manage IDA licenses.                                                                                                 │
│ plugin                Manage IDA Pro plugins.                                                                                              │
│ share                 Share files with Hex-Rays.                                                                                           │
╰────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────╯
```


<details>
<summary>All Available Commands</summary>

```bash
$ hcli commands
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ Command                   ┃ Description                                                      ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ hcli auth default         │ Set or show the default credentials.                             │
│ hcli auth key create      │ Create a new API key.                                            │
│ hcli auth key install     │ Install an API key as a new credentials.                         │
│ hcli auth key list        │ List all API keys.                                               │
│ hcli auth key revoke      │ Revoke an API key.                                               │
│ hcli auth list            │ List all credentials.                                            │
│ hcli auth switch          │ Switch the default credentials.                                  │
│ hcli commands             │ List all available command combinations.                         │
│ hcli download             │ Download IDA binaries, SDKs, and utilities.                      │
│ hcli extension create     │ Create an hcli extension                                         │
│ hcli extension list       │ List hcli extensions                                             │
│ hcli ida install          │ Installs IDA unattended.                                         │
│ hcli ida set-default      │ Set or show the default IDA installation directory.              │
│ hcli license get          │ Download license files with optional filtering.                  │
│ hcli license install      │ Install a license file to an IDA Pro installation directory.     │
│ hcli license list         │ List available licenses with rich formatting.                    │
│ hcli login                │ Log in to the Hex-Rays portal and create new credentials.        │
│ hcli logout               │ Log out and remove stored credentials.                           │
│ hcli plugin config del    │ Delete a plugin configuration setting.                           │
│ hcli plugin config export │ Export plugin configuration settings as JSON.                    │
│ hcli plugin config get    │ Get a plugin configuration setting.                              │
│ hcli plugin config import │ Import plugin configuration settings from JSON.                  │
│ hcli plugin config list   │ List all configuration settings for a plugin.                    │
│ hcli plugin config set    │ Set a plugin configuration setting.                              │
│ hcli plugin install       │ No description available                                         │
│ hcli plugin lint          │ Lint an IDA plugin directory, archive (.zip file), or HTTPS URL. │
│ hcli plugin repo snapshot │ Create a snapshot of the repository.                             │
│ hcli plugin search        │ No description available                                         │
│ hcli plugin status        │ No description available                                         │
│ hcli plugin uninstall     │ No description available                                         │
│ hcli plugin upgrade       │ No description available                                         │
│ hcli share delete         │ Delete shared file by code.                                      │
│ hcli share get            │ Download a shared file using its shortcode.                      │
│ hcli share list           │ List and manage your shared files.                               │
│ hcli share put            │ Upload a shared file.                                            │
│ hcli update               │ Check for hcli updates.                                          │
│ hcli whoami               │ Display the currently logged-in user.                            │
└───────────────────────────┴──────────────────────────────────────────────────────────────────┘
```

</details>


## Examples

### Install IDA

First, lets check what licenses are associated with our account:

```bash
$ hcli license list

Subscription Licenses (2):
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ ID              ┃ Edition          ┃ Type  ┃ Status ┃ Expiration ┃ Addons                           ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 96-XXXX-XXXX-01 │ IDA Essential PC │ named │ Active │ 2026-08-25 │ 2 decompiler(s)                  │
│ 96-XXXX-XXXX-02 │ IDA Ultimate     │ named │ Active │ 2026-07-02 │ 11 decompiler(s) + TEAMS, LUMINA │
└─────────────────┴──────────────────┴───────┴────────┴────────────┴──────────────────────────────────┘
```

Now lets download the IDA installer, though we'll see in a subsequent step we can also download it on-demand:
   

```bash
$ hcli download
Fetching available downloads...
Current path: /
? Select an item to navigate or download: 📁 release
Current path: /release
? Select an item to navigate or download: 📁 9.2
Current path: /release/9.2
? Select an item to navigate or download: 📁 ida-pro
Current path: /release/9.2/ida-pro
? Select an item to navigate or download: (Use arrow keys, type to filter)
   ← Go back
   📄 License Server 9.2 (hexlicsrv92_x64linux.run)
 » 📄 IDA Pro Mac Apple Silicon 9.2 (ida-pro_92_armmac.app.zip)
   📄 Lumina Server 9.2 (lumina92_x64linux.run)
   📄 Teams Server 9.2 (hexvault92_x64linux.run)
   📄 IDA Pro Windows 9.2 (ida-pro_92_x64win.exe)
   📄 IDA Pro Linux 9.2 (ida-pro_92_x64linux.run)
   📄 IDA Pro Mac Intel 9.2 (ida-pro_92_x64mac.app.zip)
Getting download URL for: release/9.2/ida-pro/ida-pro_92_armmac.app.zip
Starting download of release/9.2/ida-pro/ida-pro_92_armmac.app.zip...
Using cached file: /Users/user/.hcli/cache/ida-pro_92_armmac.app.zip
Download complete! File saved to: ida-pro_92_armmac.app.zip
Successfully downloaded 1 file(s)

$ ls -lah *.app.zip
-rw-r--r--@ 1 user  staff   539M Sep 12 13:47 ida-pro_92_armmac.app.zip
```

For a little context, here are the options that the automated installer supports:

```bash
$ hcli ida install --help

 Usage: hcli ida install [OPTIONS] [INSTALLER]

 Installs IDA unattended.

╭─ Options ──────────────────────────────────────────────────────────────────────────╮
│ --yes          -y        Auto-accept confirmation prompts                          │
│ --dry-run                Show what would be done without actually installing       │
│ --set-default            Mark this IDA installation as the default                 │
│ --accept-eula  -a        Accept EULA                                               │
│ --install-dir  -i  TEXT  Install dir                                               │
│ --license-id   -l  TEXT  License ID (e.g., 48-307B-71D4-46)                        │
│ --download-id  -d  TEXT  Installer slug                                            │
│ --help                   Show this message and exit.                               │
╰────────────────────────────────────────────────────────────────────────────────────╯
```
   
Now lets run the automated installer, which doesn't show any dialog or popups - really convenient!

Note:
- we're setting this as the "default" IDA installation, so this is what idalib and the plugin manager will use
- in this example we set `--dry-run`, but you should remove this in real-life
- HCLI also fetches and installs the associated license key file so everything's ready to go
   

```bash
$ hcli ida install --set-default --accept-eula --license-id 96-0000-0000-01 ida-pro_92_armmac.app.zip --dry-run

Installation details:
  Installer: /Users/user/code/hex-rays/ida-hcli/ida-pro_92_armmac.app.zip
  Destination: /Applications/IDA Professional 9.2.app
  License: 96-0000-0000-01
  Set as default: Yes

Dry run mode - no changes will be made

Would perform the following actions:
  1. Extract installer to: /Applications/IDA Professional 9.2.app
  2. Install license to: /Applications/IDA Professional 9.2.app/Contents/MacOS
  3. Update default IDA path in: /Users/user/.idapro/ida-config.json
  4. Accept EULA
```

Now, if you know exactly which version of IDA you want, you can download and install it in a single command.
Note the use of `--download-id release/9.2/ida-pro/ida-pro_92_armmac.app.zip`, the path is derived from the `hcli download` output above.
  

```bash
$ hcli ida install --set-default --license-id 96-0000-0000-01 --download-id release/9.2/ida-pro/ida-pro_92_armmac.app.zip --dry-run

Getting download URL for: release/9.2/ida-pro/ida-pro_92_armmac.app.zip
Starting download of release/9.2/ida-pro/ida-pro_92_armmac.app.zip...
Using cached file: /Users/user/.hcli/cache/ida-pro_92_armmac.app.zip
Download complete! File saved to:
/var/folders/55/f4jb4y1d6b74cdrp_gp45hlw0000gn/T/ida-pro_92_armmac.app.zip
Successfully downloaded 1 file(s)

Installation details:
  Installer:
/var/folders/55/f4jb4y1d6b74cdrp_gp45hlw0000gn/T/ida-pro_92_armmac.app.zip
  Destination: /Applications/IDA Professional 9.2.app
  License: 96-0000-0000-01
  Set as default: Yes

Dry run mode - no changes will be made

Would perform the following actions:
  1. Extract installer to: /Applications/IDA Professional 9.2.app
  2. Install license to: /Applications/IDA Professional 9.2.app/Contents/MacOS
  3. Update default IDA path in: /Users/user/.idapro/ida-config.json
  4. Accept EULA
```

### Share a file with Hex-Rays Support

You can use HCLI to upload files into a shared space available to Hex-Rays Support.
This is really useful when you've found a bug in IDA Pro and want to help the engineers reproduce it.

There are three visibilities:

 - private: Just for me
 - domain: Anyone from my domain (@example.com)
 - authenticated: Anyone authenticated with the link

```bash
$ hcli share list
No shared files found.

$ hcli share put /tmp/1/a49e9ff8d53a9af8ef20a383a276449d.exe_.i64
? Pick a visibility 🔎 [authenticated] Anyone authenticated with the link
Upload Complete 100% 434.6/434.6 kB 2.1 MB/s 0:00:00
✓ File uploaded successfully!
Share Code: efja98
Share URL: https://my.hex-rays.com/share/efja98
Download URL: https://api.eu.hex-rays.com/api/assets/s/efja98

$ hcli share list
 » ○ a49e9ff8d53a9af8ef20a383a276449d.exe_.i64 (efja98) - 424.4 KB
```

At this point, you can share the short code (`efja98`) with support@hex-rays.com and they can access the file:
   
```bash

$ hcli share get efja98
Downloading a49e9ff8d53a9af8ef20a383a276449d.exe_.i64 100%
✓ File downloaded successfully!
File: a49e9ff8d53a9af8ef20a383a276449d.exe_.i64
Size: 424.4 KB
Saved to: a49e9ff8d53a9af8ef20a383a276449d.exe_.i64

$ hcli share delete efja98
File to delete:
  Name: a49e9ff8d53a9af8ef20a383a276449d.exe_.i64
  Code: efja98
  Size: 424.4 KB

Delete file a49e9ff8d53a9af8ef20a383a276449d.exe_.i64 ? [y/n]: y
✓ Deleted: efja98
```


### Find and Install an IDA Pro plugin




   

## Next Steps

- [License Management](../user-guide/licenses.md) - Managing your IDA licenses
- [File Sharing](../user-guide/file-sharing.md) - Share and collaborate on files
