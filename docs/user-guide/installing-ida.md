# Installing IDA

You can use HCLI to install IDA Pro unattended, non-interactively, or just conveniently.
This is great for CI/CD pipelines that rely on IDA Pro, such as testing tools that use idalib.

First, lets check what licenses are associated with our account:

```bash
$ hcli license list

Subscription Licenses (2):
┏━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━┳━━━━━━━┳━━━━━━━━┳━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┓
┃ ID              ┃ Edition          ┃ Type  ┃ Status ┃ Expiration ┃ Addons                           ┃
┡━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━╇━━━━━━━╇━━━━━━━━╇━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┩
│ 96-0000-0000-01 │ IDA Essential PC │ named │ Active │ 2026-08-25 │ 2 decompiler(s)                  │
│ 96-0000-0000-01 │ IDA Ultimate     │ named │ Active │ 2026-07-02 │ 11 decompiler(s) + TEAMS, LUMINA │
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
│ --create-python-environment  After installing IDA, create a virtual environment   │
│                              for its Python at $IDAUSR/venv                        │
│ --yes          -y            Auto-accept confirmation prompts                      │
│ --dry-run                    Show what would be done without actually installing   │
│ --set-default                Mark this IDA installation as the default             │
│ --accept-eula  -a            Accept EULA                                           │
│ --install-dir  -i  TEXT      Install dir                                           │
│ --license-id   -l  TEXT      License ID (e.g., 96-0000-0000-01)                    │
│ --download-id  -d  TEXT      Installer slug                                        │
│ --help                       Show this message and exit.                           │
╰────────────────────────────────────────────────────────────────────────────────────╯
```
   
Now lets run the automated installer, which doesn't show any dialog or popups - really convenient!

Note:

  - we're setting this as the "default" IDA installation, so this is what idalib and the plugin manager will use
  - `--create-python-environment` sets up a virtualenv for IDAPython at `$IDAUSR/venv` and configures `IDAPYTHON_VENV_EXECUTABLE`; plugins that need Python packages are installed here (see [IDA's Python Environment](ida-python-environment.md))
  - in this example we set `--dry-run`, but you should remove this in real-life
  - HCLI also fetches and installs the associated license key file so everything's ready to go
   

```bash
$ hcli ida install --set-default --create-python-environment --accept-eula --license-id 96-0000-0000-01 ida-pro_92_armmac.app.zip --dry-run

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
$ hcli ida install --set-default --create-python-environment --license-id 96-0000-0000-01 --download-id release/9.2/ida-pro/ida-pro_92_armmac.app.zip --dry-run

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
