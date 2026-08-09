"""Read the active browser tab URL without moving focus or using clipboard."""

import os
import sys
import ctypes
import urllib.request
import time
from urllib.parse import urlsplit, urlunsplit

try:
    import browser_cookie3
except ImportError:
    browser_cookie3 = None


class ActiveTabUrlError(RuntimeError):
    pass


def make_browser_download_request(url: str) -> urllib.request.Request:
    """Build a browser-like request for a URL copied from a browser tab.

    Some content delivery services, notably Dropbox's temporary ``inline2``
    links, return an error when the same URL is replayed by a non-browser user
    agent even though it remains open and valid in the browser.
    """
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/127.0.0.0 Safari/537.36"
            ),
            "Accept": "application/pdf,application/octet-stream;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        },
    )


def is_session_bound_download_url(url: str) -> bool:
    """Return whether *url* is a temporary browser-session delivery URL."""
    parsed = urlsplit(str(url or ""))
    host = (parsed.hostname or "").lower()
    return (
        host.endswith(".dl.dropboxusercontent.com")
        and parsed.path.startswith("/cd/0/")
    )


def open_with_browser_session(url: str, timeout: int = 30):
    """Open *url* with narrowly scoped cookies from an installed browser.

    Cookie access is attempted only for the URL's host and only after an
    ordinary request has shown that the delivery URL is session-bound.
    """
    if browser_cookie3 is None:
        raise RuntimeError(
            "Browser-session download support is not installed in ScanBox."
        )

    host = (urlsplit(url).hostname or "").lower()
    if not host:
        raise ValueError("The download URL has no host.")
    cookie_domain = "dropboxusercontent.com" if host.endswith(
        ".dropboxusercontent.com"
    ) else host
    failures = []
    loader_names = ("chrome", "edge", "firefox", "safari")
    for loader_name in loader_names:
        loader = getattr(browser_cookie3, loader_name, None)
        if loader is None:
            continue
        try:
            cookie_jar = loader(domain_name=cookie_domain)
            opener = urllib.request.build_opener(
                urllib.request.HTTPCookieProcessor(cookie_jar)
            )
            return opener.open(make_browser_download_request(url), timeout=timeout)
        except Exception as exc:
            failures.append(f"{loader_name}: {exc}")
    detail = "; ".join(failures) or "no supported browser profile was found"
    raise RuntimeError(f"The browser session could not download this PDF ({detail}).")


def save_active_browser_document_windows(destination: str, timeout: int = 30) -> str:
    """Use the active browser's Save As dialog to save its current document."""
    if sys.platform != "win32":
        raise RuntimeError("Browser-controlled saving is currently Windows-only.")
    from pywinauto import Desktop
    from pywinauto.keyboard import send_keys

    browser_handle = ctypes.windll.user32.GetForegroundWindow()
    if not browser_handle:
        raise RuntimeError("The active browser window was not found.")
    browser = Desktop(backend="uia").window(handle=browser_handle).wrapper_object()
    if browser.class_name() not in {"Chrome_WidgetWin_1", "MozillaWindowClass"}:
        raise RuntimeError("Keep the PDF browser window active and try again.")

    existing_dialogs = {
        window.handle
        for window in Desktop(backend="uia").windows()
        if window.class_name() == "#32770"
    }
    send_keys("^s", pause=0.05)
    deadline = time.monotonic() + min(timeout, 15)
    save_dialog = None
    while time.monotonic() < deadline:
        for window in Desktop(backend="uia").windows():
            if window.class_name() == "#32770" and window.handle not in existing_dialogs:
                save_dialog = window
                break
        if save_dialog is not None:
            break
        time.sleep(0.1)
    if save_dialog is None:
        raise RuntimeError("The browser did not open its Save As dialog.")

    filename = save_dialog.child_window(auto_id="1001", control_type="Edit")
    filename.wait("ready", timeout=5)
    filename.set_edit_text(os.path.abspath(destination))
    save_button = save_dialog.child_window(auto_id="1", control_type="Button")
    save_button.wait("enabled", timeout=5)
    save_button.invoke()

    deadline = time.monotonic() + timeout
    previous_size = -1
    stable_checks = 0
    while time.monotonic() < deadline:
        try:
            size = os.path.getsize(destination)
        except OSError:
            size = -1
        if size > 0 and size == previous_size:
            stable_checks += 1
            if stable_checks >= 3:
                return destination
        else:
            stable_checks = 0
        previous_size = size
        time.sleep(0.25)
    raise RuntimeError("The browser did not finish saving the PDF in time.")


def download_active_browser_document_windows(
    destination: str, timeout: int = 30
) -> str:
    """Invoke the browser PDF viewer's Download control and collect the file."""
    if sys.platform != "win32":
        raise RuntimeError("Browser-controlled downloading is currently Windows-only.")
    from pywinauto import Desktop

    browser_handle = ctypes.windll.user32.GetForegroundWindow()
    if not browser_handle:
        raise RuntimeError("The active browser window was not found.")
    browser = Desktop(backend="uia").window(handle=browser_handle).wrapper_object()
    if browser.class_name() not in {"Chrome_WidgetWin_1", "MozillaWindowClass"}:
        raise RuntimeError("Keep the PDF browser window active and try again.")

    downloads = os.path.join(os.path.expanduser("~"), "Downloads")
    os.makedirs(downloads, exist_ok=True)
    before = {}
    for entry in os.scandir(downloads):
        if entry.is_file():
            try:
                before[entry.path] = (entry.stat().st_mtime_ns, entry.stat().st_size)
            except OSError:
                pass

    download_button = None
    for button in browser.descendants(control_type="Button"):
        name = str(getattr(button.element_info, "name", "") or "").strip().lower()
        if name == "download" or name.startswith("download "):
            download_button = button
            break
    if download_button is None:
        raise RuntimeError("The browser PDF Download button was not accessible.")
    download_button.invoke()

    deadline = time.monotonic() + timeout
    candidate = None
    previous_size = -1
    stable_checks = 0
    while time.monotonic() < deadline:
        newest_time = -1
        for entry in os.scandir(downloads):
            if not entry.is_file() or entry.name.lower().endswith(".crdownload"):
                continue
            try:
                stat = entry.stat()
            except OSError:
                continue
            signature = (stat.st_mtime_ns, stat.st_size)
            if signature != before.get(entry.path) and stat.st_mtime_ns > newest_time:
                candidate = entry.path
                newest_time = stat.st_mtime_ns
        if candidate:
            try:
                size = os.path.getsize(candidate)
            except OSError:
                size = -1
            if size > 0 and size == previous_size:
                stable_checks += 1
                if stable_checks >= 3:
                    os.replace(candidate, os.path.abspath(destination))
                    return destination
            else:
                stable_checks = 0
            previous_size = size
        time.sleep(0.25)
    raise RuntimeError("The browser did not finish downloading the PDF in time.")


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
