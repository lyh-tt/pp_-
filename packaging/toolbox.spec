# -*- mode: python ; coding: utf-8 -*-
import sys
from pathlib import Path

PROJECT_ROOT = Path(SPECPATH).resolve().parent
IS_WIN = sys.platform == "win32"
ICON_PNG = PROJECT_ROOT / "src" / "multiscale" / "ui" / "assets" / "app_icon.png"
ICON_ICO = PROJECT_ROOT / "src" / "multiscale" / "ui" / "assets" / "app_icon.ico"

datas = [
    (str(PROJECT_ROOT / "src" / "multiscale" / "fiber" / "templates" / "fiber_post.py.tpl"), "multiscale/fiber/templates"),
    (str(PROJECT_ROOT / "src" / "multiscale" / "fiber" / "templates" / "fiber_runner.py.tpl"), "multiscale/fiber/templates"),
    (str(PROJECT_ROOT / "src" / "multiscale" / "texgen" / "templates" / "batch.py.tpl"), "multiscale/texgen/templates"),
    (str(PROJECT_ROOT / "src" / "multiscale" / "abaqus" / "templates" / "rve_post.py.tpl"), "multiscale/abaqus/templates"),
    (str(PROJECT_ROOT / "src" / "multiscale" / "abaqus" / "templates" / "abaqus_runner.py.tpl"), "multiscale/abaqus/templates"),
    (str(PROJECT_ROOT / "src" / "multiscale" / "ui" / "theme.qss"), "multiscale/ui"),
    (str(ICON_PNG), "multiscale/ui/assets"),
    (str(ICON_ICO), "multiscale/ui/assets"),
    (str(PROJECT_ROOT / "templates" / "fiber"), "templates/fiber"),
    (str(PROJECT_ROOT / "examples" / "resin_inputs_example.csv"), "examples"),
]

a = Analysis(
    [str(PROJECT_ROOT / "packaging" / "launcher.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "_pytest", "tkinter"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="材料多尺度建模处理工具箱" if IS_WIN else "multiscale-toolbox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(ICON_ICO) if IS_WIN else None,
)
