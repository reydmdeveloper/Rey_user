# -*- mode: python ; coding: utf-8 -*-
"""
PyInstaller spec for REYDM Desktop (Single EXE build).
"""

import os
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None

datas = [
    ("templates", "templates"),
    ("static", "static"),
]
# Ship .env if present
if os.path.exists(".env"):
    datas.append((".env", "."))

hiddenimports = []
hiddenimports += collect_submodules("mysql.connector")
hiddenimports += collect_submodules("waitress")
hiddenimports += collect_submodules("docx")
hiddenimports += collect_submodules("olefile")
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
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="REYDM",
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
    icon="Icon.ico" if os.path.exists("Icon.ico") else None,
)
