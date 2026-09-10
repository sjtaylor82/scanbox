"""Build an isolated, folder-based Windows ScanBox distribution."""

from pathlib import Path
import ast
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from version_resource import announcement, read_version_strings, verify_release_metadata


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

# ScanBox 2026.9.1 shipped with an empty version resource, which left screen
# readers with no version to announce for the active application. Never
# release a build whose metadata cannot answer that question.
app_constants = {}
for node in ast.parse((root / "scanbox.py").read_text(encoding="utf-8")).body:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in {"APP_NAME", "APP_VERSION"}:
                app_constants[target.id] = ast.literal_eval(node.value)
problems = verify_release_metadata(
    app_dir / "ScanBox.exe",
    app_constants["APP_NAME"],
    app_constants["APP_VERSION"],
)
if problems:
    raise SystemExit(
        "Windows build has unusable screen-reader version metadata: "
        + "; ".join(problems)
    )

print(f"ScanBox for Windows: {app_dir}")
print(
    "Screen readers will announce: "
    + announcement(read_version_strings(app_dir / "ScanBox.exe"))
)
