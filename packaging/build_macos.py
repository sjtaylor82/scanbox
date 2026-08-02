"""Build the native macOS helper and then package ScanBox.app."""

from pathlib import Path
import os
import subprocess
import sys


root = Path(__file__).resolve().parent.parent
source = root / "packaging" / "macos" / "ScanBoxMacHelper.swift"
build_root = Path.home() / "Library" / "Caches" / "ScanBox"
helper_dir = build_root / "macos-helper"
helper = helper_dir / "scanbox-macos-helper"
helper_dir.mkdir(parents=True, exist_ok=True)
work_dir = build_root / "pyinstaller"
dist_dir = build_root / "dist"
work_dir.mkdir(parents=True, exist_ok=True)
dist_dir.mkdir(parents=True, exist_ok=True)

build_environment = os.environ.copy()
build_environment["SCANBOX_MACOS_HELPER_BUILD"] = str(helper)

subprocess.run(
    [
        "xcrun",
        "swiftc",
        "-parse-as-library",
        "-O",
        str(source),
        "-o",
        str(helper),
        "-framework",
        "AppKit",
        "-framework",
        "AVFoundation",
        "-framework",
        "CoreGraphics",
        "-framework",
        "CoreImage",
        "-framework",
        "CoreMedia",
        "-framework",
        "ImageCaptureCore",
        "-framework",
        "ImageIO",
        "-framework",
        "PDFKit",
        "-framework",
        "ScreenCaptureKit",
        "-framework",
        "UniformTypeIdentifiers",
        "-framework",
        "Vision",
    ],
    check=True,
    cwd=root,
    env=build_environment,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--workpath",
        str(work_dir),
        "--distpath",
        str(dist_dir),
        "packaging/ScanBox-macOS.spec",
    ],
    check=True,
    cwd=root,
    env=build_environment,
)
print(f"ScanBox.app: {dist_dir / 'ScanBox.app'}")
