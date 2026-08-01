"""Build the native macOS helper and then package ScanBox.app."""

from pathlib import Path
import subprocess
import sys


root = Path(__file__).resolve().parent.parent
source = root / "packaging" / "macos" / "ScanBoxMacHelper.swift"
helper_dir = root / "build" / "macos-helper"
helper = helper_dir / "scanbox-macos-helper"
helper_dir.mkdir(parents=True, exist_ok=True)

subprocess.run(
    [
        "xcrun",
        "swiftc",
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
        "ImageCaptureCore",
        "-framework",
        "ImageIO",
        "-framework",
        "ScreenCaptureKit",
        "-framework",
        "UniformTypeIdentifiers",
        "-framework",
        "Vision",
    ],
    check=True,
    cwd=root,
)
subprocess.run(
    [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "packaging/ScanBox-macOS.spec",
    ],
    check=True,
    cwd=root,
)
