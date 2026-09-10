"""Build an isolated, folder-based Windows ScanBox distribution."""

from pathlib import Path
import ast
import os
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from version_resource import (
    announcement, read_version_strings, release_targets, verify_release_metadata,
)


root = Path(__file__).resolve().parent.parent
venv_dir = root / "temp" / "windows-build-venv"
venv_python = venv_dir / "Scripts" / "python.exe"

if os.name != "nt":
    raise SystemExit("The Windows build must be created on Windows.")

if not venv_python.is_file():
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

if os.environ.get("SCANBOX_SKIP_BUILD_INSTALL") != "1":
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
app_name = app_constants["APP_NAME"]
app_version = app_constants["APP_VERSION"]

# A screen reader asked to identify the active application reads the file that
# created the focused window, which for a wxPython program is wx's compiled
# core rather than ScanBox.exe. wxPython ships that file with no version
# resource at all, so stamp ScanBox's own identity into every file that could
# be asked. Use the build interpreter, which already has PyInstaller.
targets = release_targets(app_dir)
if len(targets) < 2:
    raise SystemExit(
        "Could not find wxPython's compiled core in the build; a screen reader "
        "would have no version to announce for the active application."
    )
for target in targets:
    if read_version_strings(target):
        continue
    subprocess.run(
        [str(venv_python), str(Path(__file__).resolve().parent / "version_resource.py"),
         "--stamp", str(target), app_name, app_version],
        check=True,
        cwd=root,
    )

# Stamping rewrites a binary that Python has to load. Prove the packaged
# wxPython still imports before this build is allowed to become a release.
check = subprocess.run(
    [str(venv_python), "-c",
     "import sys; sys.path.insert(0, sys.argv[1]); import wx; print(wx.version())",
     str(app_dir / "_internal")],
    capture_output=True, text=True, cwd=root,
)
if check.returncode != 0:
    raise SystemExit(
        "The packaged wxPython no longer imports after stamping:" + chr(10) + check.stderr
    )
print(f"Packaged wxPython still imports: {check.stdout.strip()}")

for target in targets:
    problems = verify_release_metadata(target, app_name, app_version)
    if problems:
        raise SystemExit(
            f"{target.name} has unusable screen-reader version metadata: "
            + "; ".join(problems)
        )

print(f"ScanBox for Windows: {app_dir}")
for target in targets:
    print(
        f"  {target.name} announces: "
        + announcement(read_version_strings(target))
    )
