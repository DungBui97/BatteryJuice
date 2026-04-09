# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for BatteryJuice
# Build: pyinstaller batteryjuice.spec

import sys
from pathlib import Path

block_cipher = None

a = Analysis(
    ['src/app.py'],
    pathex=[str(Path('src').resolve())],
    binaries=[],
    datas=[
        ('config.json', '.'),
        ('assets/chart.umd.min.js', 'assets'),
    ],
    hiddenimports=[
        'rumps',
        'objc',
        'Foundation',
        'AppKit',
        'Cocoa',
        'plistlib',
        'sqlite3',
        'json',
        'csv',
        # our modules
        'collector',
        'database',
        'analyzer',
        'reporter',
    ],
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
    name='BatteryJuice',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='BatteryJuice',
)

app = BUNDLE(
    coll,
    name='BatteryJuice.app',
    icon=None,  # TODO: add assets/icon.icns for custom app icon
    bundle_identifier='com.batteryjuice',
    info_plist={
        'CFBundleName': 'BatteryJuice',
        'CFBundleDisplayName': 'BatteryJuice',
        'CFBundleVersion': '1.0.0',
        'CFBundleShortVersionString': '1.0.0',
        'LSUIElement': True,          # menu bar only, no Dock icon
        'NSHighResolutionCapable': True,
        'NSAppleEventsUsageDescription': 'BatteryJuice uses Apple Events to read battery data.',
    },
)
