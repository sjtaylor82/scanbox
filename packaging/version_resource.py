"""Read Windows executable version metadata the way a screen reader does.

JAWS answers Ctrl+Insert+V by reading the version resource of the executable
that owns the focused window, so ScanBox's reported version comes entirely
from the metadata compiled into ScanBox.exe. ScanBox 2026.9.1 shipped with an
empty version resource and JAWS had nothing to announce.

Use this module to gate a release build, and run it directly to reproduce the
screen-reader lookup against a running ScanBox:

    python packaging/version_resource.py                 # focused application
    python packaging/version_resource.py path\to\ScanBox.exe
"""

from pathlib import Path
import ctypes
import ctypes.wintypes
import struct
import sys
import time


# The fields a screen reader may voice. FileDescription and FileVersion
# carry the announcement; CompanyName and LegalCopyright are read only to
# confirm they are absent, because they would lengthen it.
REPORTED_FIELDS = (
    "ProductName",
    "ProductVersion",
    "FileVersion",
    "FileDescription",
    "CompanyName",
    "LegalCopyright",
)


def _require_windows():
    if sys.platform != "win32":
        raise RuntimeError("Windows version resources can only be read on Windows.")


def read_version_strings(path):
    """Return the version-resource strings compiled into a Windows binary."""
    _require_windows()
    path = str(Path(path))
    version = ctypes.WinDLL("version", use_last_error=True)
    version.GetFileVersionInfoSizeW.argtypes = [
        ctypes.wintypes.LPCWSTR, ctypes.POINTER(ctypes.wintypes.DWORD)
    ]
    version.GetFileVersionInfoSizeW.restype = ctypes.wintypes.DWORD
    version.GetFileVersionInfoW.argtypes = [
        ctypes.wintypes.LPCWSTR, ctypes.wintypes.DWORD,
        ctypes.wintypes.DWORD, ctypes.c_void_p,
    ]
    version.VerQueryValueW.argtypes = [
        ctypes.c_void_p, ctypes.wintypes.LPCWSTR,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.wintypes.UINT),
    ]

    size = version.GetFileVersionInfoSizeW(path, None)
    if not size:
        # No VERSIONINFO resource at all: this is the 2026.9.1 failure.
        return {}
    buffer = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(path, 0, size, buffer):
        raise ctypes.WinError(ctypes.get_last_error())

    value = ctypes.c_void_p()
    length = ctypes.wintypes.UINT()
    if not version.VerQueryValueW(
        buffer, "\VarFileInfo\Translation",
        ctypes.byref(value), ctypes.byref(length),
    ) or length.value < 4:
        return {}
    language, codepage = struct.unpack("<HH", ctypes.string_at(value.value, 4))

    strings = {}
    for field in REPORTED_FIELDS:
        query = f"\StringFileInfo\{language:04x}{codepage:04x}\{field}"
        if version.VerQueryValueW(
            buffer, query, ctypes.byref(value), ctypes.byref(length)
        ) and length.value:
            # The reported length includes the terminating null character.
            text = ctypes.wstring_at(value.value, length.value - 1).strip()
            if text:
                strings[field] = text
    return strings


def foreground_application_path():
    """Return the executable behind the focused window, as JAWS resolves it."""
    _require_windows()
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.restype = ctypes.wintypes.HWND
    kernel32.OpenProcess.restype = ctypes.wintypes.HANDLE

    window = user32.GetForegroundWindow()
    if not window:
        raise RuntimeError("No window currently has focus.")
    process_id = ctypes.wintypes.DWORD()
    user32.GetWindowThreadProcessId(window, ctypes.byref(process_id))
    # PROCESS_QUERY_LIMITED_INFORMATION works without elevation.
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        size = ctypes.wintypes.DWORD(32768)
        name = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(
            handle, 0, name, ctypes.byref(size)
        ):
            raise ctypes.WinError(ctypes.get_last_error())
        return name.value
    finally:
        kernel32.CloseHandle(handle)


def announcement(strings):
    """Return the phrase a screen reader builds from these fields.

    JAWS supplies the word "version" itself, so a resource naming only the
    application and its number is read as "ScanBox version 2026.9.3".
    """
    name = strings.get("FileDescription") or strings.get("ProductName")
    number = strings.get("FileVersion") or strings.get("ProductVersion")
    if not name or not number:
        return ""
    return f"{name} version {number}"


def verify_release_metadata(path, app_name, app_version):
    """Return the problems that would spoil the Ctrl+Insert+V announcement."""
    strings = read_version_strings(path)
    if not strings:
        return ["the executable has no readable version resource"]
    problems = []
    expected = {
        "ProductName": app_name,
        "ProductVersion": app_version,
        "FileVersion": app_version,
        "FileDescription": app_name,
    }
    for field, want in expected.items():
        got = strings.get(field, "")
        if got != want:
            problems.append(f"{field} is {got!r}, expected {want!r}")
    # The announcement must be the application and its version, nothing more.
    # A publisher or licence line here is read out before the user reaches the
    # number they asked for.
    for field in ("CompanyName", "LegalCopyright"):
        if strings.get(field):
            problems.append(
                f"{field} is set to {strings[field]!r} and would pad the "
                "spoken version announcement"
            )
    wanted = f"{app_name} version {app_version}"
    spoken = announcement(strings)
    if spoken != wanted:
        problems.append(f"would be announced as {spoken!r}, expected {wanted!r}")
    return problems


def _main(argv):
    if argv:
        target = argv[0]
    else:
        # Give the operator time to focus ScanBox, mirroring the moment a JAWS
        # user presses the keystroke.
        print("Focus the ScanBox window; reading the foreground application in 5 seconds...")
        time.sleep(5)
        target = foreground_application_path()
    print(f"Executable: {target}")
    strings = read_version_strings(target)
    if not strings:
        print("No version resource. A screen reader has nothing to announce.")
        return 1
    for field in REPORTED_FIELDS:
        print(f"  {field}: {strings.get(field, '(missing)')}")
    spoken = announcement(strings)
    print(chr(10) + 'A screen reader would announce: ' + (spoken or '(nothing)'))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
