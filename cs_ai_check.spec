# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_all

# customtkinter 自带主题 JSON 与字体资源，必须显式收集
ctk_datas, ctk_binaries, ctk_hidden = collect_all('customtkinter')

a = Analysis(
    ['src/app.py'],
    pathex=[],
    binaries=ctk_binaries,
    datas=[('src/assets/icon.ico', 'assets')] + ctk_datas,
    hiddenimports=ctk_hidden,
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
    name='cs_ai_check',
    icon='src/assets/icon.ico',
    version='version_info.txt',
    manifest='cs_ai_check.manifest',
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
)
