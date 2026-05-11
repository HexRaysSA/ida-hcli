# HCLI plugin bundle specification

## Goal

HCLI must be able to install IDA plugins and their Python dependencies on machines that cannot reach the network. A connected machine builds a plugin bundle archive. An offline machine can copy that archive locally and use normal plugin manager commands against it.

The plugin bundle is repository-level. It may contain multiple plugins, multiple versions, and the shared wheelhouses (directories of pre-downloaded `.whl` files) needed to install those plugins. It is not a per-plugin sandbox, and it does not change HCLI's current dependency installation model: Python dependencies are installed into the active IDA Python environment.

## Supported user flows

Create a plugin bundle on a connected machine targeting the current machine's platform and Python:

```console
hcli plugin bundle create \
  --path malware-vm-tools-2026-04.hcli-plugin-bundle.zip \
  --platform current --python current \
  oplog==0.1.3 \
  hint-calls==0.1.3
```

Both `--platform` and `--python` are always required. The special value `current` resolves to the current machine's platform or Python version. The special value `all` resolves to all supported platforms (linux, windows, macos-arm64, macos-intel) or all supported Python versions (3.10–3.14). Otherwise, pass a specific name like `linux` or version like `3.12`.

Create a plugin bundle for all platforms at specific Python versions:

```console
hcli plugin bundle create \
  --path bundle.zip \
  --platform all --python 3.12 --python 3.13 \
  oplog==0.1.3 \
  hint-calls==0.1.3
```

Create a plugin bundle for specific platforms and Python versions:

```console
hcli plugin bundle create \
  --path malware-vm-tools-2026-04.hcli-plugin-bundle.zip \
  --platform linux --platform windows --platform macos-arm64 \
  --python 3.12 --python 3.13 \
  oplog==0.1.3 \
  hint-calls==0.1.3
```

`--platform` and `--python` are both repeatable. HCLI builds the cross product of all specified platforms and Python versions. Platform accepts short aliases: `linux`, `windows`, `macos-arm64`, `macos-intel`, `win`, etc. Duplicate targets from overlapping aliases (e.g. `--platform all --platform linux`) are deduplicated.

Create a plugin bundle that includes local/private plugin archives:

```console
hcli plugin bundle create \
  --path internal-review.hcli-plugin-bundle.zip \
  --platform current --python current \
  --repo /path/to/internal-plugin-repository.json \
  ./dist/internal-plugin-1.2.0.zip \
  private-helper==2.0.0
```

Inspect or search a plugin bundle without manual extraction:

```console
hcli plugin --repo ./malware-vm-tools-2026-04.hcli-plugin-bundle.zip search
hcli plugin --repo ./malware-vm-tools-2026-04.hcli-plugin-bundle.zip search hints
```

Install from a plugin bundle on an offline machine:

```console
hcli plugin --repo ./malware-vm-tools-2026-04.hcli-plugin-bundle.zip install hint-calls
```

Upgrade from a plugin bundle:

```console
hcli plugin --repo ./malware-vm-tools-2026-04.hcli-plugin-bundle.zip upgrade hint-calls
```

In plugin bundle mode, upgrade means "upgrade to the newest compatible version available in this plugin bundle." It does not mean latest upstream.

### Advanced usage

Use a corporate pip index instead of the default pip configuration:

```console
hcli plugin --pip-index-url https://pypi.example.corp/simple install ipyida
```

Use an explicit local wheel source:

```console
hcli plugin --pip-find-links /mnt/wheelhouse --offline install ipyida
```

## Command-line interface

The plugin command group gains these options:

```console
hcli plugin [--repo REPO] [--pip-index-url URL] [--pip-extra-index-url URL] [--pip-find-links PATH_OR_URL] [--offline] COMMAND ...
```

`--repo` accepts the existing repository forms plus a local plugin bundle archive. A plugin bundle archive is a ZIP file containing top-level `plugin-bundle.json`.

`--pip-index-url` is passed to pip as `--index-url`. It may point to PyPI, Artifactory, or any other compatible package index.

`--pip-extra-index-url` is repeatable and passed to pip as `--extra-index-url`.

`--pip-find-links` is repeatable and passed to pip as `--find-links`.

`--offline` forces pip dependency resolution to use only local sources. It passes `--no-index` and must be used with either a plugin bundle wheelhouse or at least one `--pip-find-links` value.

Pip source precedence is:

1. Explicit pip CLI options.
2. Plugin bundle wheelhouse selected from the active plugin bundle.
3. Existing configured/default online pip behavior.

If explicit pip CLI options are provided while using a plugin bundle repository, the explicit options control dependency installation. The plugin bundle still controls plugin discovery and plugin archive bytes.

## Plugin bundle archive format

A plugin bundle archive is a ZIP file with this layout:

```text
plugin-bundle.json
plugins/
  <archive-name>.zip
dependencies/
  python/
    <target-id>/
      <wheel files>.whl
```

The `plugins/` directory contains unmodified IDA plugin ZIP archives. HCLI discovers bundled plugins by walking `plugins/` and reading `ida-plugin.json` from each archive.

Each `dependencies/python/<target-id>/` directory is a flat wheelhouse for one target tuple. Wheels keep their original filenames. Sdists are rejected by default during bundle creation.

Each target entry in the manifest is a snapshot of the exact compatibility parameters used to fetch wheels for that target. This allows audit and install to verify wheel compatibility without re-deriving the parameters.

`plugin-bundle.json` is UTF-8 JSON. Version 1 has this shape:

```json
{
  "version": 1,
  "kind": "hcli-plugin-bundle",
  "builtAt": "2026-04-28T16:00:00Z",
  "createdBy": {
    "tool": "hcli",
    "version": "0.0.0"
  },
  "targetPlatformTags": [
    {
      "id": "linux-x86_64-cp312",
      "idaPlatform": "linux-x86_64",
      "pythonVersion": "3.12",
      "implementation": "cp",
      "abis": ["cp312", "abi3", "none"],
      "pipPlatformTags": [
        "manylinux_2_28_x86_64",
        "manylinux_2_17_x86_64",
        "manylinux2014_x86_64"
      ],
      "wheelhouse": "dependencies/python/linux-x86_64-cp312"
    }
  ]
}
```

## Target matching

On install or upgrade, HCLI detects the active IDA platform and active IDA Python interpreter. For IDA 9.0+, the first supported Python line is Python 3.10+.

HCLI selects a wheelhouse whose target tuple matches:

- HCLI/IDA platform, such as `windows-x86_64` or `macos-aarch64`
- Python major/minor version
- CPython implementation and ABI expectations

If no target tuple matches, HCLI fails before changing plugins or installing dependencies.

A plugin bundle may contain target tuples for operating systems and Python versions other than the machine that created it. Cross-target creation is supported when every dependency can be obtained as a compatible wheel for the requested target. If a required dependency is available only as an sdist, or only as a wheel with a platform baseline newer than the target, bundle creation or audit must fail for that target unless the user supplies a matching prebuilt wheel.

### IDA version compatibility during bundle creation

Bundle creation does not check IDA version compatibility. The builder machine may run a different IDA version than the target machines, and plugin `idaVersions` metadata is not a useful filtering axis during packaging. Plugins are bundled as-is; IDA version compatibility is checked only at install time on the target machine. This avoids adding IDA version as yet another dimension to the target matrix (alongside platform and Python version) and keeps the bundle creation model simple. If users report problems with this approach, IDA version filtering can be revisited.

## Dependency installation behavior

When installing a plugin with `pythonDependencies`, HCLI installs dependencies with pip before extracting the plugin. When a plugin bundle is in use, HCLI extracts the matching wheelhouse to a temporary directory and adds it as a `--find-links` source. By default pip can still reach online indexes, so a bundle with an incomplete wheelhouse will fall back to downloading missing wheels. When the user passes `--offline`, HCLI adds `--no-index` so pip resolves only from local sources:

```console
python -m pip install \
  --isolated \
  --disable-pip-version-check \
  --no-cache-dir \
  [--no-index]           # only when --offline is passed \
  --find-links <temporary-wheelhouse> \
  <all dependency specs>
```

HCLI includes dependency specs from already installed HCLI-managed plugins plus the plugin being installed or upgraded. Already installed packages may satisfy requirements if pip considers them compatible. HCLI does not use `--ignore-installed` by default.

No hash pinning is required. The user trusts the plugin bundle author.

HCLI does not uninstall Python dependencies when plugins are uninstalled. Orphaned dependencies are left in the Python environment, matching current behavior.

## Bundle creation behavior

`hcli plugin bundle create` accepts explicit plugin versions and local plugin ZIP paths as positional arguments. Repository plugin references must include an exact version, for example `oplog==0.1.3`. Bare latest resolution is intentionally not part of the first version. `--path` selects the output archive path.

Targeting uses `--platform` and `--python`, both required and repeatable. Each accepts `current` (auto-detect this machine), `all` (all supported values), or a specific value. `--platform` accepts the canonical IDA platform name (e.g. `linux-x86_64`) or short aliases (`linux`, `windows`, `macos-arm64`, `macos-intel`). `--python` accepts a `major.minor` version string (e.g. `3.12`). HCLI builds the cross product of all resolved platforms and Python versions. Duplicate targets are deduplicated.

The supported Python versions for `--python all` are maintained as a hardcoded constant (`SUPPORTED_PYTHON_VERSIONS`): 3.10, 3.11, 3.12, 3.13, 3.14. This is updated when new Python versions release (annually in October). This matches the approach used by cibuildwheel and other ecosystem tools — no package provides this list dynamically.

A legacy `--target` flag (e.g. `--target linux-x86_64-cp312`) is accepted for scripting but hidden from help. It cannot be combined with `--platform` or `--python`.

Bundle creation resolves all selected plugin archives, reads their `ida-plugin.json` metadata, collects all `pythonDependencies`, and materializes a flat wheelhouse per target tuple. A single online Linux builder can create wheelhouses for all target platforms when all dependencies publish compatible wheels.

The preferred wheelhouse materialization path is one `pip download` invocation per target. For cross-target downloads HCLI must pass all compatibility options together:

```console
python -m pip download \
  --only-binary=:all: \
  --implementation cp \
  --python-version <major.minor> \
  --abi <cp-abi> --abi abi3 --abi none \
  --platform <platform-tag> [--platform <platform-tag> ...] \
  --dest <wheelhouse> \
  -r <requirements-or-lock>
```

`--platform` is repeatable. Linux targets should list every manylinux tag HCLI accepts, for example `manylinux_2_28_x86_64`, `manylinux_2_17_x86_64`, and `manylinux2014_x86_64`. macOS x86_64 targets should include the supported deployment baseline. Optional targets such as Windows ARM64 or musllinux should be explicit presets rather than inferred from x86_64 targets. Over-listing compatible tags is safer than under-listing them.

`uv pip compile --universal` may be used to produce one cross-platform lock or constraints file before materializing target wheelhouses. `uv pip install --python-platform ... --python ... --target ...` is also valid for producing an unpacked per-target site-packages tree; because the version 1 plugin bundle format stores wheel files, `pip download` remains the direct path for wheelhouse plugin bundles unless HCLI later adds an unpacked dependency tree format.

By default, bundle creation rejects sdists because offline installation from sdists requires build tools and build dependencies on the target. Missing wheels are handled by failing the affected target, by using a matching builder to produce wheels, or by supplying prebuilt wheels with a local `--find-links` source. Linux wheels can often be built from one Linux host inside a manylinux container. Windows and macOS sdist-only dependencies require native runners or prebuilt vendor wheels.

If selected plugins have incompatible Python dependency constraints, bundle creation fails.

## Additive plugin bundles

Plugin bundles are additive. An offline VM may use one plugin bundle today and a second plugin bundle later. HCLI does not require a single authoritative plugin bundle for the machine.

When a plugin bundle install fails, errors should distinguish where possible: whether the plugin is absent from the bundle, whether it exists but is incompatible with the current IDA version or platform, whether no wheelhouse matches the active IDA Python, whether a dependency wheel is missing from the selected wheelhouse, or whether dependency constraints conflict with already installed packages.

## Private plugin metadata

The `repository` field in `ida-plugin.json` must be a valid GitHub URL (`https://github.com/org/project`). This applies to both public and private plugins. Private plugins should use a GitHub repository URL — either a public repo or a private one. If no real repository exists, a placeholder GitHub URL is acceptable (e.g. `https://github.com/internal/placeholder`) because the public plugin indexer only fetches URLs for plugins it ingests, and offline bundle plugins are never indexed.

The validation is intentionally not relaxed to support arbitrary hosting providers (GitLab, Gitea, etc.) until there are concrete user requirements. Keeping the GitHub constraint preserves the field's usefulness as a consistent, predictable link format across all plugins.

## Plugin spec disambiguation

When the plugin repository contains multiple entries with the same plugin name from different hosts (e.g. forks), `bundle create` requires the `@host` suffix to disambiguate:

```console
hcli plugin bundle create \
  --path bundle.zip \
  --platform all --python all \
  "efiXplorer==6.2.0@https://github.com/rehints/efixplorer" \
  "idalib-rust-bindings==0.9.0@https://github.com/idalib-rs/idalib"
```

The `@host` is parsed from the spec before resolution. Unambiguous plugins do not require it.

## Known limitations observed during all-plugin bundle testing

Bundle creation pools all `pythonDependencies` from every included plugin into a single `pip download` invocation per target. A single unresolvable dependency (nonexistent version, missing wheel for the target) fails the entire wheelhouse for that target. There is no per-plugin isolation.

Plugins observed with broken or unavailable dependencies at the time of testing (2026-05-04):

- `Frida_Tools`, `FridaTools`: require `pyside6>=6.10.2` (no such version on PyPI)
- `idapcode`: requires `pypcode~=3.3.3` (no such version on PyPI; available versions max at 1.1.2)
- `IDAssist`: requires `pysqlite3` (no binary wheels for macOS x86_64 or cp310)
- `ida-cyberchef`: requires `STPyV8` (no binary wheels for cp310 targets)
- `fwhunt-ida`: requires `fwhunt-scan~=2.3.5` which depends on `uefi-firmware>=1.10` (no wheels for macOS aarch64)

Additionally, `codeload.github.com` URLs produce archives with non-deterministic hashes, causing SHA256 verification failures for plugins that use them as download URLs (e.g. `xray`). This is a plugin repository data issue, not a bundle code issue.

Python 3.14 targets often fail because many packages have not yet published cp314 wheels (as of 2026-05-04). The `--python all` flag includes 3.14 by default; use explicit `--python 3.10 --python 3.11 --python 3.12 --python 3.13` to avoid these failures until ecosystem support improves.

A future improvement could add a `--skip-on-error` flag to bundle creation so that individual plugin dependency failures are reported as warnings rather than aborting the entire build.

## Out of scope

The first version does not address per-plugin dependency sandboxes, dependency removal on uninstall, reproducible builds, or wheel redistribution and license compliance. Plugin bundle signatures and external checksum files are also deferred, as is storing corporate pip index settings in `ida-config.json`.

## See also

- [Plugin packaging and format](./plugin-packaging-and-format.md) — the individual plugin archive format that bundles wrap
- [Plugin repository architecture](./plugin-repository-architecture.md) — the online repository that bundles replace for offline use
- [CI/CD Integration](../advanced/ci-cd-integration.md) — building bundles in automated pipelines
