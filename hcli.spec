# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules, copy_metadata
from PyInstaller.depend.bindepend import get_python_library_path
import os
import sys

artifact_name = os.environ.get("ARTIFACT_NAME", "hcli")

datas = []
datas += copy_metadata('ida-hcli')

binaries = []
if sys.platform == "win32":
    python3_dll = os.path.join(os.path.dirname(get_python_library_path()), "python3.dll")
    assert os.path.exists(python3_dll), f"python3.dll not found next to the interpreter: {python3_dll}"
    binaries.append((python3_dll, "."))


a = Analysis(
    ['src/hcli/main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=collect_submodules('rich._unicode_data'),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name=artifact_name,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
