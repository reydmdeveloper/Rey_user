# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for REYDM Desktop.

Build with:
    pyinstaller REYDM.spec

Output:
    dist/REYDM/REYDM(.exe)        (one-folder build — recommended, faster start)

Notes
-----
• Flask needs `templates/` and `static/` available at runtime, so they are
  added as data files. The launcher (desktop_app.py) resolves them via
  sys._MEIPASS when frozen.
• mysql-connector-python and waitress have submodules PyInstaller may miss,
  so they're declared as hidden imports.
• QtWebEngine resources are collected automatically by the PyQt6 hooks, but we
  also pull in PyQt6.QtWebEngineCore explicitly to be safe.
"""

import os
from PyInstaller.utils.hooks import collect_submodules, collect_data_files

block_cipher = None

datas = [
    ("templates", "templates"),
    ("static", "static"),
]
# Ship a .env if present so creds travel with the build (optional — remove this
# line if you'd rather configure the database on each machine separately).
if os.path.exists(".env"):
    datas.append((".env", "."))

hiddenimports = []
hiddenimports += collect_submodules("mysql.connector")
hiddenimports += collect_submodules("waitress")
hiddenimports += [
    "pytz",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebEngineWidgets",
]

a = Analysis(
    ["desktop_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="REYDM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,  # set True temporarily if you need to see server logs
    disable_windowed_traceback=False,
    icon=os.path.join("static", "Images", "REYDM_LOGO.png")
    if os.path.exists(os.path.join("static", "Images", "REYDM_LOGO.png"))
    else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="REYDM",
)
