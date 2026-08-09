"""Read the active browser tab URL without moving focus or using clipboard."""

import os
import sys
import ctypes
from urllib.parse import urlsplit, urlunsplit


class ActiveTabUrlError(RuntimeError):
    pass


def warm_up_active_tab_url_reader():
    """Initialize thread-affine Windows UI Automation before the first shortcut."""
    if sys.platform == "win32":
        from pywinauto import Desktop
        Desktop(backend="uia")


def normalize_web_url(value: str) -> str:
    value = str(value or "").strip()
    if not value or any(character.isspace() for character in value):
        raise ActiveTabUrlError("The browser did not expose a valid web address.")
    if "://" not in value:
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
        raise ActiveTabUrlError("The active tab is not an HTTP or HTTPS page.")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc, parsed.path or "/", parsed.query, ""))


def get_active_url_windows() -> str:
    try:
        from pywinauto import Desktop
    except ImportError as exc:
        raise ActiveTabUrlError("Windows browser integration is not installed.") from exc

    window_handle = ctypes.windll.user32.GetForegroundWindow()
    if not window_handle:
        raise ActiveTabUrlError("No active window was found.")
    window_spec = Desktop(backend="uia").window(handle=window_handle)
    window = window_spec.wrapper_object()
    process_id_value = ctypes.c_ulong()
    ctypes.windll.user32.GetWindowThreadProcessId(
        window_handle, ctypes.byref(process_id_value)
    )
    process_id = int(process_id_value.value)
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, process_id)
    executable = ""
    if process:
        try:
            size = ctypes.c_ulong(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if ctypes.windll.kernel32.QueryFullProcessImageNameW(
                process, 0, buffer, ctypes.byref(size)
            ):
                executable = os.path.basename(buffer.value).lower()
        finally:
            ctypes.windll.kernel32.CloseHandle(process)
    if executable not in {"chrome.exe", "msedge.exe", "firefox.exe"}:
        raise ActiveTabUrlError("The foreground application is not a supported browser.")
    class_name = window.class_name()
    if class_name not in {"Chrome_WidgetWin_1", "MozillaWindowClass"}:
        raise ActiveTabUrlError("The foreground application is not a supported browser.")

    direct_specs = (
        [window_spec.child_window(auto_id="urlbar-input", control_type="Edit")]
        if executable == "firefox.exe"
        else [
            window_spec.child_window(
                title="Address and search bar", control_type="Edit"
            )
        ]
    )
    for spec in direct_specs:
        try:
            if not spec.exists(timeout=0.75):
                continue
            edit = spec.wrapper_object()
            try:
                return normalize_web_url(edit.get_value())
            except Exception:
                return normalize_web_url(edit.window_text())
        except Exception:
            continue

    # Browser versions and localized accessibility labels vary. Retain a
    # broader fallback, but only after the fast browser-specific query fails.
    candidates = []
    for edit in window.descendants(control_type="Edit"):
        info = edit.element_info
        identity = " ".join(filter(None, (
            getattr(info, "name", ""), getattr(info, "automation_id", ""),
        ))).lower()
        if any(marker in identity for marker in (
            "address", "search bar", "urlbar", "omnibox", "location",
        )):
            candidates.insert(0, edit)
        else:
            candidates.append(edit)
    for edit in candidates:
        try:
            value = edit.get_value()
        except Exception:
            value = edit.window_text()
        try:
            return normalize_web_url(value)
        except ActiveTabUrlError:
            continue
    raise ActiveTabUrlError("The browser did not expose its address bar through UI Automation.")


def _ax_value(element, attribute):
    from ApplicationServices import AXUIElementCopyAttributeValue
    error, value = AXUIElementCopyAttributeValue(element, attribute, None)
    return None if error else value


def get_active_url_macos(prompt_for_permission=True) -> str:
    from AppKit import NSWorkspace
    from ApplicationServices import (
        AXIsProcessTrustedWithOptions, AXUIElementCreateApplication,
        kAXTrustedCheckOptionPrompt,
    )
    trusted = AXIsProcessTrustedWithOptions({
        kAXTrustedCheckOptionPrompt: bool(prompt_for_permission)
    })
    if not trusted:
        raise ActiveTabUrlError(
            "Allow ScanBox under System Settings > Privacy & Security > Accessibility, then reopen it."
        )
    application = NSWorkspace.sharedWorkspace().frontmostApplication()
    bundle_id = application.bundleIdentifier() if application else ""
    if bundle_id not in {
        "com.apple.Safari", "com.google.Chrome", "com.microsoft.edgemac",
        "org.mozilla.firefox",
    }:
        raise ActiveTabUrlError("The foreground application is not a supported browser.")
    app_ref = AXUIElementCreateApplication(application.processIdentifier())
    root = _ax_value(app_ref, "AXFocusedWindow")
    if root is None:
        raise ActiveTabUrlError("The active browser window is not accessible.")
    queue, visited = [root], set()
    while queue and len(visited) < 1500:
        element = queue.pop(0)
        identity = id(element)
        if identity in visited:
            continue
        visited.add(identity)
        role = str(_ax_value(element, "AXRole") or "")
        label = " ".join(str(_ax_value(element, name) or "") for name in (
            "AXDescription", "AXTitle", "AXIdentifier",
        )).lower()
        if role in {"AXTextField", "AXComboBox"} and any(
            marker in label for marker in ("address", "search", "url", "location")
        ):
            try:
                return normalize_web_url(_ax_value(element, "AXValue"))
            except ActiveTabUrlError:
                pass
        queue.extend(_ax_value(element, "AXChildren") or [])
    raise ActiveTabUrlError("The browser did not expose its address bar through Accessibility.")


def get_active_tab_url() -> str:
    if sys.platform == "win32":
        return get_active_url_windows()
    if sys.platform == "darwin":
        return get_active_url_macos()
    raise ActiveTabUrlError(f"Unsupported platform: {sys.platform}")


if __name__ == "__main__":
    try:
        print(get_active_tab_url())
    except ActiveTabUrlError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        raise SystemExit(1)
