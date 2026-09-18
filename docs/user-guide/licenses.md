# Managing Licenses

## Overview

HCLI lists the IDA licenses your account is entitled to, downloads their license
files, and copies those files into an IDA installation.

Three commands cover this:

| Command | What it does | Login required |
| --- | --- | --- |
| `hcli license list` | Shows your licenses and their details | Yes |
| `hcli license get` | Downloads license files | Yes |
| `hcli license install` | Copies a license file into an IDA directory | No |

## Viewing Licenses

List the licenses available to you:

```bash
hcli license list
```

If your account has access to more than one customer, HCLI asks which one to use.
Licenses are grouped as **Perpetual** and **Subscription**, and each table shows
the license ID, edition, type, status, expiration and add-ons.

Restrict the list to one plan:

```bash
hcli license list --plan subscription
hcli license list --plan legacy
```

`--plan legacy` selects perpetual licenses. There is no command that shows a
single license in more detail; `hcli license list` is the complete view.

## Downloading License Files

`hcli license get` downloads license files to a directory. Only licenses whose
status is `active` can be downloaded.

Choose interactively from your active licenses:

```bash
hcli license get
```

When more than one license matches, HCLI shows a checkbox list so you can select
several at once.

Download one specific license:

```bash
hcli license get --id 96-0000-0000-01
```

Download every active license into a directory:

```bash
hcli license get --all --output-dir ./licenses
```

The options are:

| Option | Meaning |
| --- | --- |
| `-i`, `--id` | License ID, for example `96-0000-0000-01` |
| `-p`, `--plan` | `subscription` or `legacy` |
| `-t`, `--type` | Product code, for example `IDAPRO`, `IDAHOME`, `LICENSE_SERVER` |
| `-a`, `--all` | Take every match instead of prompting |
| `--output-dir` | Where to write the files (default: the current directory) |

License files are named after the license, for example
`idapro_96-0000-0000-01.hexlic`.

## Installing Licenses

### Automatic: during IDA installation

`hcli ida install --license-id` downloads the matching license file and copies it
into the new installation for you:

```bash
hcli ida install --license-id 96-0000-0000-01 ida-pro_92_armmac.app.zip
```

This is the recommended path. See [Installing IDA](installing-ida.md).

### Manual: an existing installation

Download the license file, then install it:

```bash
hcli license get --id 96-0000-0000-01
hcli license install idapro_96-0000-0000-01.hexlic
```

The file is a positional argument. With no second argument, HCLI asks where to put
it:

```
Where do you want to install the license?
  1. /Users/user/.idapro (user directory)
  2. /Applications/IDA Professional 9.2.app
  3. Other (specify custom path)
? Select installation:
```

The list offers your IDA user directory, the IDA installations HCLI detects, and a
custom path. If the directory you choose does not exist, HCLI offers to create it.

Give the target directory directly to skip the prompt:

```bash
hcli license install idapro_96-0000-0000-01.hexlic ~/.idapro
```

`hcli license install` only copies the file. It does not download anything, so it
works without logging in, and it does not change any IDA configuration.

## License Locations

The IDA user directory is the default suggestion:

- **Windows**: `%APPDATA%\Hex-Rays\IDA Pro\`
- **macOS** and **Linux**: `~/.idapro/`

`HCLI_IDAUSR`, or `IDAUSR` if the first is unset, overrides this location. See
[Environment Variables](../reference/environment-variables.md).

A license can also live in an installation directory rather than the user
directory, which is where `hcli ida install --license-id` puts it. On macOS that is
the `Contents/MacOS` directory inside the application bundle.

## Troubleshooting

### No licenses found

Check that you are logged in as the right user:

```bash
hcli whoami
```

Then check your entitlements:

```bash
hcli license list
```

If `hcli license list` shows a license but `hcli license get` does not offer it,
the license is not `active`. Only active licenses can be downloaded.

### Installation issues

- Close IDA before replacing a license file.
- Check that you can write to the target directory.
- Confirm the file is the `.hexlic` file HCLI downloaded, not a renamed copy:
  `hcli ida install --license-id` looks for a name ending in
  `<license-id>.hexlic`.
