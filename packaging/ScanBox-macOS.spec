# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import cv2


project = Path(SPECPATH).parent
config_dir = project / "config"

datas = [
    (str(project / "manual.html"), "."),
    (str(project / "LICENSE"), "."),
    (str(project / "SHUTTER.WAV"), "."),
    (str(config_dir / "vision_pack_florence2_base.json"), "config"),
    (str(config_dir / "vision_pack_qwen3_vl_2b.json"), "config"),
    (str(project / "examples"), "examples"),
]

face_cascades = [
    Path(cv2.data.haarcascades) / name
    for name in (
        "haarcascade_frontalface_default.xml",
    )
]
for face_cascade in face_cascades:
    if not face_cascade.is_file():
        raise SystemExit(
            f"OpenCV face-position detector is missing: {face_cascade}"
        )
    datas.append((str(face_cascade), "cv2/data"))

helper = project / "build" / "macos-helper" / "scanbox-macos-helper"
if not helper.is_file():
    raise SystemExit(
        "macOS helper is missing. Run: python packaging/build_macos.py"
    )

a = Analysis(
    [str(project / "scanbox.py")],
    pathex=[str(project)],
    binaries=[
        (str(helper), "macos"),
    ],
    datas=datas,
    hiddenimports=[
        "fitz",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["win32com", "winreg"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScanBox",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

app = BUNDLE(
    exe,
    a.binaries,
    a.datas,
    name="ScanBox.app",
    bundle_identifier="au.com.scanbox.ScanBox",
    info_plist={
        "CFBundleDisplayName": "ScanBox",
        "CFBundleShortVersionString": "2026.8.0",
        "CFBundleVersion": "2026.8.0",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSCameraUsageDescription": (
            "ScanBox uses the selected camera to capture documents and "
            "photographs for local processing."
        ),
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
    entitlements_file=str(project / "packaging" / "macos-entitlements.plist"),
)
