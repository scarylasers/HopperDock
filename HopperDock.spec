# -*- mode: python ; coding: utf-8 -*-

import os, tkinterdnd2
_tkdnd_dir = os.path.join(os.path.dirname(tkinterdnd2.__file__), 'tkdnd')

a = Analysis(
    ['window_dock.pyw'],
    pathex=[],
    binaries=[],
    datas=[
        # Project-local copies — no absolute C:\icons paths, so the build
        # works on any machine that checks this repo out.
        ('hopper-dock square logo with background.png', '.'),
        ('hopper-dock square logo with background.ico', '.'),
        ('hopper-dock square logo.png', '.'),  # transparent — in-dock bunny
        # Starter scripts the default "Scripts" category points at
        ('examples', 'examples'),
        # Bundle the tkdnd Tcl extension so OLE drag-drop works in the exe
        (_tkdnd_dir, 'tkinterdnd2/tkdnd'),
    ],
    hiddenimports=[
        'PIL', 'PIL._tkinter_finder',
        'pystray', 'pystray._win32',
        'tkinterdnd2',
    ],
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
    name='HopperDock',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['hopper-dock square logo with background.ico'],
)
