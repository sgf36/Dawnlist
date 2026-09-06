# PyInstaller spec for Dawnlist.
# Build from the project root with:
#   .venv\Scripts\python.exe -m PyInstaller packaging\build_exe.spec --noconfirm
#
# Builds in --onedir mode (a folder, not a single self-extracting exe).
# Onefile builds unpack themselves into a temp directory at every launch, which
# is a strong heuristic signal antivirus and SmartScreen use to flag packers and
# droppers. Onedir avoids that runtime self-extraction. It reduces false
# positives for an unsigned build; only code signing removes the warning.

import sys
from pathlib import Path

block_cipher = None
project_root = Path(SPECPATH).parent
icons_dir = project_root / "packaging" / "icons"

# --------------------------------------------------------------------------
# Locale catalogues — the single most important line in this file.
#
# app/i18n.py resolves catalogues as `Path(__file__).parent / "resources" /
# "locales"`. That works from source and silently resolves to nothing once
# frozen, because the .py files live inside the archive. If the catalogues are
# not collected HERE, every locale falls back to English and the app looks
# monolingual — with no error, because `_load_catalog` deliberately returns {}
# for a missing file rather than crashing the UI.
#
# EasyPost shipped a variant of exactly this failure: build flags were created
# in the source tree but never bundled, so the paid build launched with no
# licence gate at all. Anything read by path at runtime must appear below.
# --------------------------------------------------------------------------
datas = [
    (str(project_root / "app" / "resources" / "locales"), "app/resources/locales"),
]

# Build-variant flags, listed individually and conditionally. Copying the whole
# resources directory would sweep every variant's flag into every build, which
# is the opposite of what these are for. The variants are mutually exclusive:
#   direct download : license_required.flag
#   Microsoft Store : store_build.flag  -> production gated behind the Store add-on
#   Mac App Store   : mas_build.flag
datas += [
    (str(project_root / "app" / "resources" / name), "app/resources")
    for name in ("license_required.flag", "store_build.flag", "mas_build.flag")
    if (project_root / "app" / "resources" / name).exists()
]

hiddenimports = [
    # Imported lazily inside build_provider/build_send so the engine stays
    # importable without them; PyInstaller's static graph therefore never sees
    # them and would leave them out of the frozen app.
    "keyring",
    "keyring.backends.Windows",
    "keyring.backends.macOS",
    "anthropic",
]

binaries = []

# The Store build reads Windows.Services.Store through the winrt packages.
# app/core/store_entitlement.py (when added) imports them lazily inside
# try/except, so the static graph never sees them and the shipped Store build
# would be unable to read its entitlement — production would stay locked for
# every customer.
if sys.platform.startswith("win"):
    try:
        from PyInstaller.utils.hooks import collect_all
        w_datas, w_binaries, w_hidden = collect_all("winrt")
        datas += w_datas
        binaries += w_binaries
        hiddenimports += w_hidden
    except Exception as exc:  # never break the direct build over this
        print(f"[build_exe.spec] winrt collect_all skipped: {exc}")

# The Mac App Store build reads its StoreKit entitlement through PyObjC, also
# lazily. Absent on a notarised-.dmg build, which is a harmless no-op.
if sys.platform == "darwin":
    for _mod in ("StoreKit", "Foundation", "CoreFoundation", "objc"):
        hiddenimports.append(_mod)

# Declares the process Per-Monitor v2 DPI-aware at the manifest level, so
# Windows applies awareness at process creation. This is what clears the WACK
# DPIAwarenessValidation warning and keeps the UI crisp on scaled displays.
gui_manifest = project_root / "packaging" / "Dawnlist.exe.manifest"

a = Analysis(
    [str(project_root / "app" / "main.py")],
    pathex=[str(project_root)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # PySide6 ships far more than this app uses. Excluding the heavy unused
    # modules keeps the download and the Store package materially smaller.
    excludes=[
        "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtMultimedia", "PySide6.QtQuick",
        "PySide6.QtQml", "PySide6.QtCharts", "PySide6.QtDataVisualization",
        "tkinter", "matplotlib", "numpy", "pytest",
    ],
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Dawnlist",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,               # UPX compression is itself an AV heuristic signal
    console=False,           # GUI app: no console window
    disable_windowed_traceback=False,
    icon=str(icons_dir / "dawnlist.ico") if (icons_dir / "dawnlist.ico").exists() else None,
    manifest=str(gui_manifest) if gui_manifest.exists() else None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Dawnlist",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="Dawnlist.app",
        icon=str(icons_dir / "dawnlist.icns") if (icons_dir / "dawnlist.icns").exists() else None,
        bundle_identifier="com.spencerfields.dawnlist",
        info_plist={
            "CFBundleName": "Dawnlist",
            "CFBundleDisplayName": "Dawnlist",
            "NSHighResolutionCapable": True,
            # No microphone, camera, contacts or location usage strings: the
            # app requests none of those, and declaring one it does not use is
            # a review rejection.
            "LSMinimumSystemVersion": "12.0",
        },
    )
