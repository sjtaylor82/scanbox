"""Build an isolated, folder-based Windows ScanBox distribution."""

from pathlib import Path
import os
import subprocess
import sys


root = Path(__file__).resolve().parent.parent
venv_dir = root / "temp" / "windows-build-venv"
venv_python = venv_dir / "Scripts" / "python.exe"

if os.name != "nt":
    raise SystemExit("The Windows build must be created on Windows.")

if not venv_python.is_file():
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

subprocess.run(
    [
        str(venv_python),
        "-m",
        "pip",
        "install",
        "--disable-pip-version-check",
        "-r",
        str(root / "requirements.txt"),
        "pyinstaller",
    ],
    check=True,
    cwd=root,
)
subprocess.run(
    [
        str(venv_python),
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "packaging/ScanBox-Windows.spec",
    ],
    check=True,
    cwd=root,
)
app_dir = root / "dist" / "ScanBox"
internal_dir = app_dir / "_internal"
required_paths = [
    app_dir / "ScanBox.exe",
    internal_dir / "cv2" / "data" / "haarcascade_frontalface_default.xml",
]
missing = [str(path) for path in required_paths if not path.is_file()]
if missing:
    raise SystemExit("Windows build is missing required files: " + ", ".join(missing))
if list(internal_dir.rglob("opencv_videoio_ffmpeg*")):
    raise SystemExit("Windows build unexpectedly contains the unused FFmpeg backend.")
print(f"ScanBox for Windows: {app_dir}")
