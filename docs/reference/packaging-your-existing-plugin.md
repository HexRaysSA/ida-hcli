# Publishing your plugin to the IDA Plugin Repository

## Why Publish Your Plugin?

As the author of an existing or new plugin, we encourage you to make the following updates so that your project is compatible with the HCLI and Plugin Manager, and can benefit from the new ecosystem. 

Making your plugin available via Plugin Manager offers several benefits:

  - simplified plugin installation
  - improved plugin discoverability through the central index
  - easy Python dependency management


If you have any questions, open an issue on [this repo](https://github.com/HexRaysSA/ida-hcli) or contact our [support](https://support.hex-rays.com/).


!!! note "Coming Soon"

      We plan to add plugin configuration management to handle things like user preferences, API key storage, etc.


If you're having trouble or don't have the bandwidth, please don't hesitate to reach out to us at Hex-Rays - we're here to support you.

## Steps to Publication

The key points to make your IDA plugin available via Plugin Manager are:

  1. Update `ida-plugin.json`
  2. Package your plugin into a ZIP archive
  3. Publish releases on GitHub

For further details on how your plugin fits into the greater ecosystem, you can also review:

  - [Plugin packaging and format](./plugin-packaging-and-format.md)
  - [Plugin repository architecture](./plugin-repository-architecture.md)


The plugin ecosystem is fully automated. After you publish a new GitHub release with updated metadata, your plugin will automatically be indexed and made available to IDA users.


### 1. Update or create `ida-plugin.json`

IDA 9.0 introduced `ida-plugin.json` as a way to [declare metadata about plugins](https://docs.hex-rays.com/user-guide/plugins/plugin-submission-guide#define-plugin-metadata-with-ida-plugin.json), such as name and entry point.
We extend this format with **new required and optional fields** to ensure compatibility with HCLI and the Plugin Manager, and to make the metadata more informative.

A minimal `ida-plugin.json` file now looks like:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "plugin1",
    "version": "1.0.0",
    "entryPoint": "plugin1.py",
    "urls": {
      "repository": "https://github.com/HexRaysSA/ida-hcli"
    },
    "authors": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }]
  }
}
```

and a more complete `ida-plugin.json` looks like:

```json
{
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "ida-terminal-plugin",
    "entryPoint": "index.py",
    "version": "1.0.0",
    "idaVersions": [
      "9.0",
      "9.1",
      "9.2"
    ],
    "platforms": [
      "windows-x86_64",
      "linux-x86_64",
      "macos-x86_64",
      "macos-aarch64",
    ],
    "description": "A lightweight terminal integration for IDA Pro that lets you open a fully functional terminal within the IDA GUI.\nQuickly access shell commands, scripts, or tooling without leaving your reversing environment.",
    "license": "MIT",
    "logoPath": "ida-plugin.png",
    "categories": [
      "ui-ux-and-visualization"
    ],
    "keywords": [
      "terminal",
      "shell",
      "cli",
    ],
    "pythonDependencies": [
      "pydantic>=2.12"
    ],
    "urls": {
      "repository": "https://github.com/williballenthin/idawilli"
    },
    "authors": [{
      "name": "Pierre-Alexandre Losson",
      "email": "palosson@hex-rays.com"
    }],
    "maintainers": [{
      "name": "Willi Ballenthin",
      "email": "wballenthin@hex-rays.com"
    }],
    "settings": [
      {
        "key": "theme",
        "type": "string",
        "required": true,
        "default": "darcula",
        "name": "color theme",
        "documentation": "the color theme name, picked from https://windowsterminalthemes.dev/",
      }
    ]
  }
}
```

Here are the **new required** fields:

  - `.plugin.version` is now required
  - `.plugin.urls.repository` is required
  - `.plugin.authors` and/or `.plugin.maintainers` must be provided, `.email` is required

and new optional fields:

  - `.plugin.pythonDependencies` is a list of packages on PyPI that will be installed
  - `.plugin.keywords` is a list of terms to help users searching for plugins
  - `.plugin.platforms` is recommended, defaults to all platforms. The possible values are: `windows-x86_64`, `linux-x86_64`, `macos-x86_64`, and `macos-aarch64`.
  - `.plugin.license` for the code license of your project
  - `.plugin.settings` is a list of descriptors of settings


!!! tip "Validating Your `ida-plugin.json` File"

    If there's a problem with the `ida-plugin.json` file, then it will not be indexed by the plugin repository. Unfortunately even things like trailing commas will break strict JSON parsers like the one used by HCLI.  
    We recommend to use `hcli plugin lint /path/to/plugin[.zip]` to check for problems and suggestions.


### 2. Package your plugin

You must distribute plugins via ZIP archives.
See [Plugin packaging and format](./plugin-packaging-and-format.md) for more details on the contents of the ZIP archives.
Essentially, collect the plugin files (Python source or `.dll`/`.so`/`.dylib` shared objects) and the `ida-plugin.json` and any other supporting resources into a single file.

- For **pure-Python plugins**, you can probably rely on the source archive automatically attached to GitHub Releases and Tags.
- For **native plugins**, you should consider using GitHub Actions to build the artifacts - and Hex-Rays is happy to provide templates and/or propose an initial workflow. You can see an example here: [milankovo/zydisinfo .../build.yml](https://github.com/milankovo/zydisinfo/blob/main/.github/workflows/build.yml)

#### Packaging Pure Python Plugins

For simple single-file plugins, all you need to do is add an `ida-plugin.json` file.
Then do releases via GitHub Actions and the automatically attached source archive will be the plugin archive.

In fact, most pure Python plugins can get away with no build step, and just tagging releases via GitHub Releases.

If there are multiple plugins in the same repo, this is ok, as long as they're in separate directories.
See "Multi-Plugin Archives" in [Plugin packaging and format](./plugin-packaging-and-format.md).

If a Python plugin relies on a third-party dependency, declare this in the `pythonDependencies` array in `ida-plugin.json`.

If there's many files related to the plugin, ensure they're all in the same directory (or nested subdirectory) as `ida-plugin.json`.
Python plugins can rely on imports relative to the entry point script, so the following are ok:

```
plugins.zip
└── plugin1
    ├── ida-plugin.json
    ├── plugin_entry.py
    └── myutils.py

# import myutils
```

or

```
plugins.zip
└── plugin1
    ├── ida-plugin.json
    ├── plugin_entry.py
    └── mylib
        ├── __init__.py
        └── foo

# import mylib.foo
```

Some plugins have published most of their code to PyPI via a traditional Python package, and then refer to this in a trivial entrypoint stub.
This is fine. IPyIDA is an example.
They can keep doing this, updating `pythonDependencies` to reference that package; or they can package the Python code as a relative import.


#### Packaging a Native Plugin

If its a native plugin, adapt the build configuration (if it exists) to GitHub Actions.
To acquire the SDK, either use a Git submodule or use HCLI to fetch it.
The latter is probably a better solution but requires an active IDA Pro license (but you can get one through the [Plugin Contributor Program](https://hex-rays.com/contributor-program)),
and enables you to build against 8.4, 9.0, 9.1, as well as 9.2+.
The open source SDK on GitHub only has tags for 9.2+.

Here's an example workflow: [PR#4](https://github.com/milankovo/zydisinfo/pull/4)

Once you have built the shared object files, package them up along with the `ida-plugin.json` file.
You can create one artifact per platform, or do a "fat" binary archive (see above).
Separate files might be easier; just make sure you set `.plugin.platforms` in `ida-plugin.json` to reflect the contents, so we don't try to install .dll files on macOS.

### 3. Publish releases on GitHub

In the near term, you must use GitHub Releases to tag releases, because the plugin repository backend (initially) uses GitHub to discover and index available plugins.
You'll need to upload the plugin archives as attachments to the release (unless the default source archive is sufficient, which is usually true for pure-Python plugins).

Each day, an indexer looks for GitHub repositories that contain `ida-plugin.json` and inspects the releases for candidate plugins.
So once you've updated the metadata file and made a new release in GitHub, your plugin will soon show up automatically!

In other words, inclusion into the plugin repository is completely self-service - it's just a matter of putting a well-formed plugin in a place we can find it.

!!! tip "Test your plugin"

      Check out [our guide](../how-to/test-plugin-before-publishing.md) to test your plugin before publishing it to the new Plugin Manager ecosystem.


## FAQ

### Do I need to submit my plugin anywhere?

No, if you're using GitHub Releases to share plugin ZIP archives with valid `ida-plugin.json` files, then your plugins should be auto-included into the Hex-Rays plugin repository!
While there used to be an explicit submission process via My Hex-Rays portal, we've moved to a self-service flow that's (hopefully) open and transparent.

In the future, we may re-open a web form or accept PRs to add plugins not hosted on GitHub, but this isn't supported quite yet.

### What do I do if my plugin doesn't show up in the repo?

1. Ensure the plugin file (the ZIP archive) is well formed: `hcli plugin lint /path/to/plugin.zip`
2. Check that plugin is recognized by the indexer by inspecting [plugin-repository.json](https://github.com/HexRaysSA/plugin-repository/blob/v1/plugin-repository.json).
    1. ensure you can find your plugin in the [search results](https://github.com/search?q=path%3A**%2Fida-plugin.json&type=code)
    1. otherwise, you can propose to add your plugin to the explicit list here: [known-repositories.json](https://github.com/HexRaysSA/plugin-repository/blob/v1/known-repositories.txt)
3. Open an issue [here](https://github.com/HexRaysSA/plugin-repository/issues) and Hex-Rays will be happy to help debug


The indexer runs daily, so it might take a little time for your plugin to show up.
Feel free to ping us and we can manually trigger a rebuild to check for your new plugin.


### How can I report a suspicious/malicious plugin?

Open an issue on the plugin repository and the Hex-Rays team will investigate and potentially update the index.


### How does moderation work?

Plugins can be removed from the plugin repository so that HCLI does not serve them to users.

At a technical level, the plugin repository *is* the JSON file available [here](https://github.com/HexRaysSA/plugin-repository).
Its produced by an indexer that runs periodically, and uses a [denylist](https://github.com/HexRaysSA/plugin-repository/blob/v1/ignored-repositories.txt) to filter out repositories that Hex-Rays deems inappropriate (malware, illegal, abusive, etc.).
Because the entire repository is within a git repository on GitHub, anyone can review the enforcement actions (such as adding to the denylist) and see what has happened.
We designed transparency into the system from the start, to help everyone understand and trust the infrastructure.

### I previously submitted my plugin via My Hex-Rays portal. What should I do now?

Follow the guidelines on [making your plugin compatible with Plugin Manager](../reference/packaging-your-existing-plugin.md). Once completed, your plugin will be included in the central index and available directly through the Plugin Manager.

If your plugin is hosted in the same repository used for your previous submission to [plugins.hex-rays.com](https://plugins.hex-rays.com/), and the repository URL remains unchanged, we’ll automatically update its details on the website.




