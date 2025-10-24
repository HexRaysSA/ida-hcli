### Experience Reports: Migrating to the Plugin Repository

#### idawilli plugins

Needed to add `ida-plugin.json files`. Used a template/copied from past projects. Took 5 minutes each.
Then did a GitHub release for the multiple IDA Python plugins.


#### IDA Terminal Plugin

Just needed to do a release in GitHub.

Took 15 minutes.


#### ipyida

Python source plugin with Python dependencies.
Notably it supports IDA 6.6+ - that will be hard to reproduce with HCLI.

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


#### mahmoudimus/ida-sigmaker

https://github.com/mahmoudimus/ida-sigmaker

ida-plugin.json had two issues:
- incorrect path for entrypoint
- path to a missing logo file

Both fixed in https://github.com/mahmoudimus/ida-sigmaker/pull/12

This demonstrates `ida-plugin.json` file doesn't receive much love today, and its easily out of date.

The author does releases, but only via tags.
So, need to add support for Python source plugins versioned with bare tags, rather than GitHub releases.


#### VirusTotal/vt-ida-plugin

https://github.com/VirusTotal/vt-ida-plugin

Doesn't have an `ida-plugin.json`. Python dependencies: `requests`.
Trivial metadata file, located in subdirectory `./plugin/`.

Still need to integrate the VT API key setting into the common framework.
Good test case for that design.

https://github.com/ida-community-plugins/vt-ida-plugin


#### mkYARA

https://github.com/fox-it/mkYARA

Needed to merge a bunch of pending PRs (the repo is not active): python3, IDA 9 support.
Needed to fix PyQt5 -> PySide6 stuff (making this IDA 9.2+ compatible).
Needed to move plugin to root so it could relative import the mkyara package;
 otherwise, would need to add pythonDependency on PyPI package that is out of date (no python3 support).

https://github.com/ida-community-plugins/mkYARA


#### yarka

https://github.com/AzzOnFire/yarka/pull/1

Very easy to port, but I had Claude do it, and it took a little bit to write ./plugins-AGENT.md.
Then I drafted the text for maintainers. This can also generally be reused.

There are some user preferences that the user can set by modifying the source code, like formatting of the yara rules.

#### Gepetto

https://github.com/JusticeRage/Gepetto/pull/105

Claude Code did it. Straightforward.

There's a config.ini file used to store settings.


#### IFL

https://github.com/HexRays-plugin-contributions/ida_ifl

Trivial single file python.
Already using GH releases.

https://github.com/HexRays-plugin-contributions/ida_ifl/releases/tag/v1.5.2

Qt error:

```
  Loading Interactive Function List...
IDAPython: Error while calling Python callback <OnCreate>:
Traceback (most recent call last):
  File "/Users/user/.idapro/plugins/IFL/ifl.py", line 1256, in OnCreate
    self.adjustColumnsToContents()
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^
  File "/Users/user/.idapro/plugins/IFL/ifl.py", line 1147, in adjustColumnsToContents
    self.addr_view.resizeColumnToContents(0)
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~^^^
  File "/Users/user/.idapro/plugins/IFL/ifl.py", line 502, in data
    elif role == QtCore.Qt.BackgroundColorRole:
                 ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
AttributeError: Error calling Python override of QAbstractTableModel::data(): type object 'PySide6.QtCore.Qt' has no attribute 'BackgroundColorRole'
```
#### Diaphora

Has a config file that points to the directory containing Diaphora.
Lots of library code, some with common names, might make sense to use relative imports,
 or otherwise reorganize code a little bit.


#### DeREFerencing

https://github.com/HexRays-plugin-contributions/deREferencing

> Config options can be modified vía deferencing/config.py file.

needed to re-add support for IDA versions less than 9.0


#### comida

https://github.com/HexRays-plugin-contributions/comida

Windows only

#### capa

https://github.com/HexRays-plugin-contributions/capa

every is provided via PyPI package. pin to specific version.
Qt errors in 9.2.
otherwise trivial.

#### xray

https://github.com/HexRays-plugin-contributions/xray

if run as a script, it installs itself.
writes a config file to $IDAUSR/$plugin.cfg

fix for Python 3.2+: https://github.com/patois/xray/pull/6

