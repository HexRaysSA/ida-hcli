
## Plugin Archive Format



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
  "IDAMetadataDescriptorVersion": 1,
  "plugin": {
    "name": "plugin1",
    "entryPoint": "entry_stub.py",
    "version": "1.0",
    "idaVersions": ">=9.0",
    "pythonDependencies": ["ida-plugin1==1.0"]
  }
}

```

And the entry stub:


```py
from ida_plugin1.ida_plugin import PLUGIN_ENTRY
```





### "Fat" Archives

Plugin archives can contain multiple compiled versions of a plugin, e.g., `foo-plugin.so` and `foo-plugin.dylib`.
In this case, the entry point must specify the bare path to the plugin, e.g., `foo-plugin`, and IDA will append the appropriate extension based on the platform.
You don't have to support all platforms. It's also ok to publish multiple "thin" archives, one for each platform.
But "fat" archives are convenient.

The file extensions must be exactly:
  - `.dll` - Windows x86-64
  - `.so` - Linux 86-64
  - `_x86_64.dylib` - macOS x86_64 (TODO: not yet supported by IDA)
  - `_aarch64.dylib` - macOS aarch64 (TODO: not yet supported by IDA)
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


## Migrating Plugins to the Plugin Repository



### Experience Reports


#### idawilli plugins

Needed to add `ida-plugin.json files`. Used a template/copied from past projects. Took 5 minutes each.
Then did a GitHub release for the multiple IDA Python plugins.


#### IDA Terminal Plugin

Just needed to do a release in GitHub.

Took 15 minutes.


#### ipyida

Python source plugin with Python dependencies.
Notably it supports IDA 6.6+ - that will be hard to reproduce with hcli.

These are actually moderately complex:

```py
      install_requires=[
          'ipykernel>=4.6',
          'ipykernel>=5.1.4; python_version >= "3.8" and platform_system=="Windows"',
          'qtconsole>=4.3',
          'qasync; python_version >= "3"',
          'jupyter-client<6.1.13',
          'nbformat',
      ],
      extras_require={
          "notebook": [
              "notebook<7",
              "jupyter-kernel-proxy",
          ]
      },
```

However, all these dependencies are packaged into the `ipyida` Python package from PyPI.


```
.
├── install_from_ida.py
├── ipyida
│   ├── __init__.py
│   ├── ida_plugin.py
│   ├── ida_qtconsole.py
│   ├── ipyida_plugin_stub.py
│   ├── kernel.py
│   └── notebook.py
├── ipyida-screenshot.png
├── LICENSE
├── pycharm-screenshot.png
├── README.adoc
├── README.virtualenv.adoc
└── setup.py
```

I think we can get away with:

- remove `install_from_ida.py`
- move `ipyida/ipyida_plugin_stub.py` -> `./ipyida_plugin_stub.py`
- add `ida-plugin.json`
  - entrypoint: `ipyida_plugin_stub.py`
  - pythonDependencies: `ipyida`

In fact, I think we can do this in a new repository and reference the `ipyida` PyPI package:
https://github.com/ida-community-plugins/hcli-ipyida

Took 45 minutes, including updating docs/references with lessons learned.


#### milankovo/ida_export_scripts

https://github.com/milankovo/ida_export_scripts

Pure Python script with existing `ida-plugin.json`. Just needs a GitHub release.
