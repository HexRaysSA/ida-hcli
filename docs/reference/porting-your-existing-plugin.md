# Porting your existing IDA Pro plugin

hcli helps users discover, install, and manage IDA Pro plugins distributed via a central index.
As the author of an existing plugin, you can make the following updates so that your project is compatible with the new ecosystem.
If you have any questions, .

We think that the hcli plugin manager brings some nice benefits to plugin authors, and that its worthwhile to update your code for:

  - much easier plugin installation
  - better plugin discovery through the central index
  - easy Python dependency management

We'll also soon add plugin configuration management to handle things like user preferences, API key storage, etc.

If you're having trouble or don't have the bandwidth, please don't hesistate to reach out to us at Hex-Rays - we're here to support you.

Anyways, the key points are:

  1. update `ida-plugin.json`
  2. package your plugin into a ZIP archive
  3. publish releases on GitHub


For further details on how your plugin fits into the greater ecosystem, you can also review:

  - [Plugin packaging and format](./plugin-packaging-and-format.md)
  - [Plugin repository architecture](./plugin-repository-architecture.md)


## 1. Update `ida-plugin.json`

IDA 9.0 introduced `ida-plugin.json` as a way to declare metadata about plugins, such as name and entry point.
We're adding new required and optional fields to make this metadata more useful.

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
    }]
  }
}
```

Here are the new required fields:

  - `.plugin.version` is now required
  - `.plugin.urls.repository` is required
  - `.plugin.authors` and/or `.plugin.maintainers` must be provided, `name` and/or `email` required

and the changed fields:

  - `.plugin.idaVersions` is recommended, and is now a list of strings, defaults to all versions

and new optional fields:

  - `.plugin.platforms` is recommended, defaults to all platforms
  - `.plugin.license`
  - `.plugin.pythonDependencies` is a list of packages on PyPI that will be installed
  - `.plugin.keywords` is a list of terms to help users searching for plugins

If there's a problem with the `ida-plugin.json` file, then it will not be indexed by the plugin repository.
Unfortunately even things like trailing commas will break strict JSON parsers like the one used by hcli.
So, you can use `hcli plugin lint /path/to/plugin[.zip]` to check for problems and suggestions.


## 2. Package your plugin


## 3. Publish releases on GitHub
  




