
## Plugin Archive Format

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

#### IDA Terminal Plugin

Just needed to do a release in GitHub.
