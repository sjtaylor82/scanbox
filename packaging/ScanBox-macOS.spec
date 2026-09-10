# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import os


project = Path(SPECPATH).parent
config_dir = project / "config"

datas = [
    (str(project / "manual.html"), "."),
    (str(project / "LICENSE"), "."),
    (str(project / "SHUTTER.WAV"), "."),
    (str(config_dir / "vision_pack_qwen3_vl_2b.json"), "config"),
    (str(project / "examples"), "examples"),
]

helper = Path(
    os.environ.get(
        "SCANBOX_MACOS_HELPER_BUILD",
        str(project / "build" / "macos-helper" / "scanbox-macos-helper"),
    )
)
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
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # Windows-only integrations.
        "accessible_output2",
        "pygrabber",
        "pythoncom",
        "pywintypes",
        "win32com",
        "win32gui",
        "win32process",
        "winreg",
        "winsdk",
        # Apple frameworks provide these jobs on macOS.
        "cv2",
        "fitz",
        "opencv_python_headless",
        "pdf_to_word",
        "pymupdf",
        # ONNX model-development packages are not used for inference.
        "onnx",
        "onnxruntime.backend",
        "onnxruntime.datasets",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
        "sympy",
        "torch",
        "transformers",
        # wxPython controls not used by ScanBox.
        "wx.aui",
        "wx.dataview",
        "wx.glcanvas",
        "wx.grid",
        "wx.html",
        "wx.media",
        "wx.richtext",
        "wx.stc",
        "wx.webview",
        "wx.xml",
        "wx.xrc",
    ],
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
        "CFBundleShortVersionString": "2026.9.2",
        "CFBundleVersion": "2026.9.2",
        "LSApplicationCategoryType": "public.app-category.productivity",
        "NSCameraUsageDescription": (
            "ScanBox uses the selected camera to capture documents and "
            "photographs for local processing."
        ),
        "NSScreenCaptureUsageDescription": (
            "ScanBox captures the active screen or window when you use its "
            "global screen description and OCR shortcuts."
        ),
        "LSMinimumSystemVersion": "14.0",
        "NSHighResolutionCapable": True,
    },
    entitlements_file=str(project / "packaging" / "macos-entitlements.plist"),
)
