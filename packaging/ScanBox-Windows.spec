# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path
import ast
import cv2
from PyInstaller.utils.win32.versioninfo import (
    VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable, StringStruct,
    VarFileInfo, VarStruct,
)


project = Path(SPECPATH).parent
# Read release constants without importing the GUI or its runtime dependencies.
app_constants = {}
for node in ast.parse((project / "scanbox.py").read_text(encoding="utf-8")).body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"APP_NAME", "APP_VERSION"}:
                app_constants[target.id] = ast.literal_eval(node.value)
app_name = app_constants["APP_NAME"]
app_version = app_constants["APP_VERSION"]
version_parts = tuple(int(part) for part in app_version.split("."))
if not 1 <= len(version_parts) <= 4 or any(not 0 <= part <= 65535 for part in version_parts):
    raise ValueError("APP_VERSION must contain 1–4 numeric components in the range 0–65535")
windows_version = version_parts + (0,) * (4 - len(version_parts))
version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=windows_version, prodvers=windows_version,
                      mask=0x3F, flags=0, OS=0x40004, fileType=1, subtype=0, date=(0, 0)),
    kids=[
        StringFileInfo([StringTable("040904B0", [
            StringStruct("ProductName", app_name),
            StringStruct("ProductVersion", app_version),
            StringStruct("FileDescription", app_name),
            StringStruct("FileVersion", app_version),
            StringStruct("InternalName", app_name),
            StringStruct("OriginalFilename", "ScanBox.exe"),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
    ],
)
config_dir = project / "config"
face_detector = Path(cv2.data.haarcascades) / "haarcascade_frontalface_default.xml"
if not face_detector.is_file():
    raise SystemExit(
        "OpenCV FaceAlign detector is missing. Use opencv-python-headless<5."
    )

datas = [
    (str(project / "packaging" / "portable_updater.ps1"), "."),
    (str(project / "manual.html"), "."),
    (str(project / "LICENSE"), "."),
    (str(project / "SHUTTER.WAV"), "."),
    (str(config_dir / "vision_pack_florence2_base.json"), "config"),
    (str(config_dir / "vision_pack_qwen3_vl_2b.json"), "config"),
    (str(face_detector), "cv2/data"),
    (str(project / "examples"), "examples"),
]

a = Analysis(
    [str(project / "scanbox.py")],
    pathex=[str(project)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "accessible_output2.outputs.auto",
        "pygrabber.dshow_graph",
        "pywinauto",
        "pywinauto.keyboard",
        "pythoncom",
        "winsdk.windows.data.pdf",
        "winsdk.windows.graphics.imaging",
        "winsdk.windows.media.ocr",
        "winsdk.windows.storage",
        "winsdk.windows.storage.streams",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        # macOS-only integrations.
        "AppKit",
        "ApplicationServices",
        "Foundation",
        "Quartz",
        # ONNX model-development packages are not used for inference.
        "onnx",
        "onnxruntime.backend",
        "onnxruntime.datasets",
        "onnxruntime.quantization",
        "onnxruntime.tools",
        "onnxruntime.transformers",
        # Optional data-analysis integrations discovered through PyMuPDF's
        # unused Table.to_pandas() helper. ScanBox uses Table.extract().
        "fsspec",
        "pandas",
        "pyarrow",
        "scipy",
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
# ScanBox opens Windows cameras explicitly through DirectShow and does not
# decode video files or network streams. The generic OpenCV wheel still ships
# its optional FFmpeg video backend, so keep it out of the release bundle.
a.binaries = [
    entry
    for entry in a.binaries
    if "opencv_videoio_ffmpeg" not in entry[0].lower()
]
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="ScanBox",
    version=version_info,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
)

bundle = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="ScanBox",
)
