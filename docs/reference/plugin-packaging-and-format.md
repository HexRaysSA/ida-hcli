## Plugin Packaging and Format

A Plugin Archive is a ZIP archive that contains an IDA plugin and its associated `ida-plugin.json` metadata file.
The metadata file should be found in the root directory of the plugin within the archive.

For example:

```
plugin.zip
├── ida-plugin.json
└── plugin.py
```

Or for a native plugin:

```
plugin.zip
├── ida-plugin.json
├── plugin.so
├── plugin.dylib
└── plugin.dll
```

### ida-plugin.json

The [`ida-plugin.json` file](https://docs.hex-rays.com/user-guide/plugins/plugin-submission-guide#define-plugin-metadata-with-ida-plugin.json) is the marker for an IDA Pro plugin.
A typical `ida-plugin.json` file might look like this:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "oplog",
    "entryPoint": "oplog_entry.py",
    "version": "0.1.2",
    "idaVersions": ["9.1", "9.2"],
    "description": "oplog is an IDA Pro plugin that records operations during analysis.",
    "license": "Apache 2.0",
    "categories": [
      "ui-ux-and-visualization"
    ],
    "pythonDependencies": ["pydantic>=2"],
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }],
    "keywords": [
      "activity-tracking",
      "workflow-analysis",
      "reverse-engineering-methodology",
      "ai-training-data",
      "analysis-visualization"
    ]
  }
}
```

And a minimal `ida-plugin.json` could look like this:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "oplog",
    "entryPoint": "oplog_entry.py",
    "version": "0.1.2",
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }]
  }
}
```

In addition to the primary fields described initially on the [Hex-Rays docs](https://docs.hex-rays.com/user-guide/plugins/plugin-submission-guide#define-plugin-metadata-with-ida-plugin.json), **HCLI compatibility added new, required fields** in `ida-plugin.json`:

  - `version`: the version of the plugin archive
  - `urls.repository`: the repository that publishes the plugin
  - `authors` (or `maintainers`): name and/or email. Social media handles are ok!

And there are new optional fields:

  - `.plugin.pythonDependencies` is a list of packages on PyPI that will be installed
  - `.plugin.keywords` is a list of terms to help users searching for plugins
  - `.plugin.platforms` is recommended, defaults to all platforms. The possible values are: `windows-x86_64`, `linux-x86_64`, `macos-x86_64`, and `macos-aarch64`.
  - `.plugin.license` for the code license of your project
  - `.plugin.settings` is a list of descriptors of settings

If there's a problem with the `ida-plugin.json` file, then the plugin is invalid and won't work with the repo.
Unfortunately even things like trailing commas will break strict JSON parsers like the one used by HCLI.
So, you can use `hcli plugin lint /path/to/plugin[.zip]` to check for problems and suggestions.


### Settings

HCLI is aware of settings that plugins declare in `ida-plugin.json` and prompts users for their value
during installation. The settings are written into `ida-config.json` and can be queried at plugin runtime
using the [ida-settings](https://pypi.org/project/ida-settings/) (v3) Python package:

```py
import ida_settings

api_key = ida_settings.get_current_plugin_setting("openai_key")
```

Plugin authors should consider moving to this configuration management system because there
 will be a single place to make edits (CLI and GUI), users don't have to manually edit source code/config files,
 and the data can be easily exported/imported.

ida-settings has an associated IDA Pro plugin `ida-settings-editor` that lets users configure plugin settings within IDA:

<img width="798"  alt="image" src="https://github.com/user-attachments/assets/fa9b2e2c-fce9-4a87-af9c-94850acaa520" />

You can install it like this:

```bash
hcli plugin install ida-settings-editor
```


### Source Archives and Binary Archives

For many pure-Python IDA plugins, source archives are often sufficient.
This means you don't have to create any GitHub Actions workflows; you just have to tag your releases in GitHub.

If you distribute your pure-Python IDA plugin via PyPI, [like IPyIDA does](https://github.com/eset/ipyida),
then the plugin directory in your source archive becomes very simple.
You can put the following content in a subdirectory of the project (preferred), or publish a separate repository for the metadata:


```
.
├── ida-plugin.json
└── entry_stub.py
```

With `ida-plugin.json`:

```json
{
  "IDAMetadataDescriptorVersion": 2,
  "plugin": {
    "name": "plugin1",
    "entryPoint": "entry_stub.py",
    "version": "1.0",
    "idaVersions": ["9.0", "9.1", "9.2"],
    "pythonDependencies": ["ida-plugin1==1.0"],
    "urls": {
      "repository": "https://github.com/foo/bar"
    },
    "authors": [{
      "email": "user@example.com"
    }]
  }
}
```

And the entry stub:


```py
from ida_plugin1.ida_plugin import PLUGIN_ENTRY
```

### "Fat" Binary Plugin Archives

Plugin archives can contain multiple compiled versions of a plugin, e.g., `foo-plugin.so` and `foo-plugin.dylib`.
In this case, the entry point must specify the bare path to the plugin, e.g., `foo-plugin`, and IDA will append the appropriate extension based on the platform.
You don't have to support all platforms. It's also ok to publish multiple "thin" archives, one for each platform.
But "fat" archives are convenient.

The file extensions must be exactly:

  - `.dll` - Windows x86-64
  - `.so` - Linux 86-64
  - `_x86_64.dylib` - macOS x86_64 (not yet supported by IDA)
  - `_aarch64.dylib` - macOS aarch64 (not yet supported by IDA)
  - `.dylib` - macOS Universal Binary, only if Intel/ARM dylibs aren't present. (note: you must use this for macOS today)

TODO: we need changes in IDA to support the non-Universal Binary paths. It only constructs paths like `.dylib` today.
TODO: we could let entry point also be a dict, mapping from platform to path. This also require changes to IDA.
However, Apple will soon drop support for Intel macs, so maybe we won't need x86_64 support for much longer.

Remember, plugin names must be unique within a plugin archive, which means:

Allowed:
```
root/
  plugin/
    ida-plugin.json (name: foo-plugin)
    foo-plugin.so
    foo-plugin.dylib
    foo-plugin.dll
```

Disallowed:
```
root/
  plugin-linux/
    ida-plugin.json (name: foo-plugin)
    foo-plugin.so
  plugin-windows/
    ida-plugin.json (name: foo-plugin)  <-- name collides!
    foo-plugin.dll
```

### (uncommon) Multi-Plugin Archives

Because the `ida-plugin.json` file marks the root of a plugin within the archive, an archive can contain multiple plugins:

```
plugins.zip
├── plugin1
│   ├── plugin1.py
│   └── ida-plugin.json
└── plugin2
    ├── plugin2.py
    └── ida-plugin.json
```

