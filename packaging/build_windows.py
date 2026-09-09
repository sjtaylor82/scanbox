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
print(f"ScanBox for Windows: {root / 'dist' / 'ScanBox'}")
