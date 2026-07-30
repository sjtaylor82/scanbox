import ctypes
from ctypes import wintypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
import winreg
import zipfile

import docx
import wx
import wx.adv
from fpdf import FPDF
from PIL import Image, ImageEnhance, ImageFilter, ImageGrab, ImageOps, UnidentifiedImageError

APP_NAME = "ScanBox"
APP_VERSION = "2026.8.0"
UPDATE_MANIFEST_URL = os.environ.get(
    "SCANBOX_UPDATE_MANIFEST_URL",
    "https://api.github.com/repos/sjtaylor82/scanbox/releases/latest",
).strip()


class _NamedPageAccessible(wx.Accessible):
    """Expose a wx.Notebook page title to MSAA clients such as JAWS.

    wx.Panel's default accessibility provider does not expose the notebook
    page label, so JAWS can see only an unnamed generic panel when a tab is
    selected. Returning the title from the provider makes the page itself
    queryable as a named property-page.
    """

    def __init__(self, window, title):
        super().__init__(window)
        self._title = title

    def GetName(self, child_id):
        if child_id == wx.ACC_SELF:
            return wx.ACC_OK, self._title
        return wx.ACC_NOT_IMPLEMENTED, ""

    def GetRole(self, child_id):
        if child_id == wx.ACC_SELF:
            return wx.ACC_OK, wx.ROLE_SYSTEM_PROPERTYPAGE
        return wx.ACC_NOT_IMPLEMENTED, wx.ROLE_SYSTEM_PANE

try:
    import numpy as np
except ImportError:
    # Needed by page/edge auto-crop and by the optional Florence-2 vision
    # engine below. Degrade gracefully if not bundled: both features just
    # become unavailable rather than crashing the app.
    np = None

try:
    import cv2
except ImportError:
    # Page/edge detection (for camera-based scanners: document cameras,
    # handheld/overhead scanners such as Pearl/IRIScan-style devices).
    # Degrade gracefully if not bundled: scanning still works, it just
    # skips the auto-crop step.
    cv2 = None

try:
    import onnxruntime as ort
except ImportError:
    # Only needed for the optional Florence-2 local vision engine (a much
    # smaller alternative to the llama.cpp GGUF models). Without it,
    # ScanBox just falls back to whichever llama.cpp pack is installed.
    ort = None

try:
    from tokenizers import Tokenizer as _HFTokenizer
except ImportError:
    _HFTokenizer = None

try:
    import fitz
except ImportError:
    fitz = None


if getattr(sys, "frozen", False):
    BASE = os.path.dirname(sys.executable)
    RESOURCE_BASE = getattr(sys, "_MEIPASS", BASE)
else:
    BASE = os.path.dirname(os.path.abspath(__file__))
    RESOURCE_BASE = BASE

MANUAL_PATH = os.path.join(RESOURCE_BASE, "manual.html")


def _is_writable(directory):
    try:
        os.makedirs(directory, exist_ok=True)
        probe = os.path.join(directory, f".write_test_{uuid.uuid4().hex}")
        with open(probe, "w"):
            pass
        os.remove(probe)
        return True
    except Exception:
        return False


# Writable per-user state lives next to the app when that folder is writable
# (portable use), otherwise under %LOCALAPPDATA%\ScanBox (e.g. when ScanBox is
# installed read-only under Program Files). Read-only bundled assets - Tesseract,
# the screen-reader DLL, the PDF2Word engine, any pre-shipped vision pack - stay
# relative to the install folder.
if _is_writable(BASE):
    DATA_DIR = BASE
else:
    DATA_DIR = os.path.join(
        os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), "ScanBox"
    )

TEMP_DIR = os.path.join(DATA_DIR, "temp")
IMAGES_DIR = os.path.join(DATA_DIR, "images")
OUTPUT_DIR = os.path.join(DATA_DIR, "output")
CONFIG_DIR = os.path.join(DATA_DIR, "config")
APP_SETTINGS_CONFIG = os.path.join(CONFIG_DIR, "settings.json")
LOG_FILE = os.path.join(OUTPUT_DIR, "scanbox.log")
VISION_CONFIG = os.path.join(CONFIG_DIR, "vision_command.txt")
VISION_PACK_MANIFEST = os.path.join(CONFIG_DIR, "vision_pack.json")
PHOTO_LIBRARY_MANIFEST = os.path.join(CONFIG_DIR, "photo_descriptions.json")
VISION_DIR = os.path.join(DATA_DIR, "engines", "vision")
BUNDLED_VISION_DIR = os.path.join(BASE, "engines", "vision")
# Florence-2-base and Florence-2-large ship files with identical names
# (vision_encoder_int8.onnx etc), so each install lives in its own
# subdirectory under VISION_DIR - this is what lets both sizes coexist and
# be switched between without re-downloading. "base" also has a flat-folder
# fallback for installs made before this subdirectory split existed.
FLORENCE_SIZE_SUBDIRS = {"base": "florence2-base", "large": "florence2-large"}
FLORENCE_MANIFEST_FILENAMES = {
    "base": "vision_pack_florence2_base.json",
    "large": "vision_pack_florence2_large.json",
}
PDF2WORD_DIR = os.path.join(BASE, "engines", "pdf2word")
PDF2WORD_EXE = os.path.join(PDF2WORD_DIR, "PDF2WORD.exe")
# Points at ScanBox's own bundled Tesseract (below) rather than a second,
# byte-identical ~87MB copy under engines\pdf2word\system\tesseract - the
# two were found to be identical, and PDF2WORD.exe only needs tesseract.exe
# reachable via PATH/TESSDATA_PREFIX, not physically alongside it.
PDF2WORD_TESSERACT_DIR = os.path.join(RESOURCE_BASE, "Tesseract")
PDF2WORD_TESSERACT = os.path.join(PDF2WORD_TESSERACT_DIR, "tesseract.exe")
PDF2WORD_TESSDATA = os.path.join(PDF2WORD_TESSERACT_DIR, "tessdata")

DEFAULT_APP_SETTINGS = {
    "delete_output_files_on_exit": False,
    "check_for_updates_on_startup": True,
    "minimize_to_notification_area": True,
    "open_word_after_pdf_conversion": False,
    "render_converted_in_window": True,
    "diagnostic_logging": False,
    "append_text_to_buffer": False,
    "photo_ocr_enabled": False,
    "show_page_headings": False,
    "last_import_dir": "",
    # "base" (smaller, quicker, less accurate) or "large" (bigger, slower,
    # more accurate). Only affects the built-in Florence-2 pack.
    "florence_model_size": "base",
}

CONF_THRESHOLD = 75
PHOTO_OCR_CONF_THRESHOLD = 75
# Cap on the OCR grounding text spliced into the describe prompt. These
# vision models are small (450M-500M params) with limited context; a long
# OCR draft (e.g. a garbled receipt) can crowd out the image tokens and the
# rest of the instructions.
OCR_DRAFT_MAX_CHARS = 600
HANDWRITING_CONF_THRESHOLD = 45
HANDWRITING_WORD_LIMIT = 8
# Keep the desktop and screen reader responsive while local AI is running.
# Multimodal inference can otherwise occupy every logical processor.
VISION_THREADS = 1
VISION_LOG_LIMIT = 12000

# Privacy-first local vision pack. SmolVLM handles general photographs while
# LFM2-VL handles low-confidence document transcription. Tesseract remains the
# first OCR pass and its draft silently grounds both models.
LLAMACPP_BUILD = "b9842"
RUNTIME_ARCHIVE = f"llama-{LLAMACPP_BUILD}-bin-win-cpu-x64.zip"

DEFAULT_VISION_PACK = {
    "name": "ScanBox compact local vision pack (SmolVLM + LFM2-VL)",
    "files": [
        {
            "name": RUNTIME_ARCHIVE,
            "url": (
                "https://github.com/ggml-org/llama.cpp/releases/download/"
                f"{LLAMACPP_BUILD}/{RUNTIME_ARCHIVE}"
            ),
            "extract": True,
        },
        {
            "name": "SmolVLM-500M-Instruct-Q8_0.gguf",
            "url": (
                "https://huggingface.co/ggml-org/SmolVLM-500M-Instruct-GGUF/"
                "resolve/main/SmolVLM-500M-Instruct-Q8_0.gguf"
            ),
        },
        {
            "name": "mmproj-SmolVLM-500M-Instruct-f16.gguf",
            "url": (
                "https://huggingface.co/ggml-org/SmolVLM-500M-Instruct-GGUF/"
                "resolve/main/mmproj-SmolVLM-500M-Instruct-f16.gguf"
            ),
        },
        {
            "name": "LFM2-VL-450M-Q8_0.gguf",
            "url": (
                "https://huggingface.co/LiquidAI/LFM2-VL-450M-GGUF/"
                "resolve/main/LFM2-VL-450M-Q8_0.gguf"
            ),
        },
        {
            "name": "mmproj-LFM2-VL-450M-Q8_0.gguf",
            "url": (
                "https://huggingface.co/LiquidAI/LFM2-VL-450M-GGUF/"
                "resolve/main/mmproj-LFM2-VL-450M-Q8_0.gguf"
            ),
        },
    ],
}

os.makedirs(TEMP_DIR, exist_ok=True)
os.makedirs(IMAGES_DIR, exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)
os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(VISION_DIR, exist_ok=True)

logger = logging.getLogger("scanbox")
logger.setLevel(logging.DEBUG)
logger.propagate = False


def configure_logging(enabled):
    """Turn the diagnostic file log on or off without duplicating handlers."""
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)
    if enabled:
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(message)s")
        )
        logger.addHandler(handler)
        logger.info("Diagnostic logging enabled; data directory: %s", DATA_DIR)

try:
    import win32com.client
except ImportError:
    win32com = None

bundled_tess = os.path.join(RESOURCE_BASE, "Tesseract", "tesseract.exe")
TESS = bundled_tess if os.path.exists(bundled_tess) else "tesseract"
tessdata = os.path.join(RESOURCE_BASE, "Tesseract", "tessdata")
if os.path.isdir(tessdata):
    os.environ["TESSDATA_PREFIX"] = tessdata


TRANSCRIBE_PROMPT = (
    "Transcribe the text in this document image as accurately as you can, "
    "keeping the line breaks and paragraph order. The OCR draft is a hint. "
    "If a word is genuinely unreadable, write [unclear] instead of guessing. "
    "Return plain text only."
)

DESCRIBE_PROMPT = (
    "Describe this photo objectively in about 120 to 220 words, using short, "
    "clear paragraphs. Begin with one sentence summarizing the whole scene, "
    "naming every person and animal present. Then describe each person and "
    "animal you named, plus any other important objects, in a logical "
    "spatial order, normally from left to right. State where they are in "
    "relation to one another and describe clearly visible expressions, "
    "posture, gestures, clothing, footwear, and colours. Only state a "
    "position such as standing, sitting, held, or on the floor/ground when "
    "it is clearly visible; if unsure of this or any other specific "
    "detail, describe it in general terms rather than guessing. "
    "Next describe the foreground, surface, background, and setting. "
    "Include distinctive lighting, shadows, weather, textures, and "
    "atmosphere when visible. Prefer specific visual details over a plain "
    "inventory, but do not repeat the summary or add a concluding recap. "
    "Omit sections that do not apply. Finish with a complete sentence before "
    "reaching the response limit. "
    "Copy clearly legible text exactly, character by "
    "character. Inspect names and stylized or cursive capital letters especially "
    "carefully. If any word or name is uncertain, omit it or say that it is "
    "unclear; never substitute or guess a similar-looking name. Describe only "
    "what is supported by the image. Reasonable impressions of mood, atmosphere, "
    "relationships, and context are welcome when they make the scene more useful "
    "or engaging; phrase uncertain interpretations naturally as impressions "
    "rather than established facts. Do not present uncertain sensitive personal "
    "attributes as facts."
)

SCREEN_DESCRIBE_PROMPT = (
    "Describe the meaningful visual content displayed in the active window as "
    "the image or scene itself. Ignore window borders, title bars, application "
    "controls, scroll bars, black margins, and other computer interface "
    "furniture. Do not introduce it as a screenshot, computer screen, window, "
    "or image with a black background unless those details are genuinely the "
    "subject of the content. "
    + DESCRIBE_PROMPT
)

# Run child processes (Tesseract, llama, PDF2Word) without flashing a console
# window when ScanBox is launched as a windowed GUI build.
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW}
AI_PROCESS_FLAGS = {
    "creationflags": (
        subprocess.CREATE_NO_WINDOW | subprocess.BELOW_NORMAL_PRIORITY_CLASS
    )
}


_win_speaker = None


def _make_speaker():
    """Build a speak(text) callable. Prefer accessible_output2, which routes to
    whichever screen reader is active (NVDA, JAWS, System Access, SAPI). Fall
    back to the bundled NVDA controller DLL, then to a silent no-op."""
    try:
        from accessible_output2.outputs.auto import Auto

        speaker = Auto()
        return lambda text: speaker.output(text)
    except Exception:
        pass
    try:
        dll_path = os.path.join(RESOURCE_BASE, "nvdaControllerClient64.dll")
        nvda = ctypes.windll.LoadLibrary(dll_path)

        def speak(text):
            if nvda.nvdaController_testIfRunning() == 0:
                nvda.nvdaController_speakText(text)

        return speak
    except Exception:
        return lambda text: None


def announce(text):
    """Announce a short status string through the active screen reader.
    Best-effort, never raises, and silent when no screen reader is running."""
    global _win_speaker
    text = (text or "")[:2000]
    if not text:
        return
    try:
        if _win_speaker is None:
            _win_speaker = _make_speaker()
        _win_speaker(text)
    except Exception:
        pass


def grab_foreground_window():
    """Capture the visible pixels occupied by the active top-level window.

    Do not use Pillow's ``window=hwnd`` capture here. Chromium and other
    hardware-accelerated applications can return a black or patterned
    placeholder through that window-capture route even while their content is
    plainly visible. Capturing the foreground window's screen rectangle reads
    the composed desktop pixels instead, including GPU-rendered web content.
    """
    hwnd = ctypes.windll.user32.GetForegroundWindow()
    if not hwnd:
        raise RuntimeError("Windows could not identify the active window.")
    rect = wintypes.RECT()
    if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        raise RuntimeError("Windows could not determine the active window bounds.")
    if rect.right <= rect.left or rect.bottom <= rect.top:
        raise RuntimeError("The active window has no visible capture area.")
    return ImageGrab.grab(
        bbox=(rect.left, rect.top, rect.right, rect.bottom),
        all_screens=True,
    )


def microsoft_word_available():
    if shutil.which("WINWORD.EXE"):
        return True
    subkey = (
        r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WINWORD.EXE"
    )
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, subkey) as key:
                executable, _kind = winreg.QueryValueEx(key, None)
            if executable and os.path.exists(executable):
                return True
        except OSError:
            continue
    return False


def read_vision_command():
    if os.path.exists(VISION_CONFIG):
        with open(VISION_CONFIG, "r", encoding="utf-8") as f:
            command = f.read().strip()
            if command:
                # Portable copies are commonly moved after the AI pack is
                # installed. Repair an old absolute runtime path automatically.
                match = re.match(r'^\s*(?:"([^"]+)"|(\S+))', command)
                executable = (match.group(1) or match.group(2)) if match else ""
                if executable and os.path.exists(executable):
                    if os.path.basename(executable).lower() == "llama-mtmd-cli.exe":
                        limited = _limit_llama_command(command)
                        if limited != command:
                            write_vision_command(limited)
                            logger.info(
                                "Updated local AI command with conservative CPU limits."
                            )
                        return limited
                repaired = build_vision_command()
                if repaired:
                    write_vision_command(repaired)
                    logger.warning(
                        "Repaired missing local AI executable %r with: %s",
                        executable,
                        repaired,
                    )
                    return repaired
                return command

    bundled = _find_in_vision(lambda n: n.lower() == "scanbox_vision.exe")
    if bundled:
        return f'"{bundled}"'

    return build_vision_command()


def write_vision_command(command):
    with open(VISION_CONFIG, "w", encoding="utf-8") as f:
        f.write(command.strip())


def read_app_settings():
    """Load settings.json, coercing each value to match its default's type
    (bool defaults stay bool, string defaults stay string, etc). Earlier this
    unconditionally cast everything to bool, which silently corrupted string
    settings like last_import_dir into True/False - fixed here before adding
    another string setting (florence_model_size)."""
    settings = dict(DEFAULT_APP_SETTINGS)
    if os.path.exists(APP_SETTINGS_CONFIG):
        try:
            with open(APP_SETTINGS_CONFIG, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            for key, value in loaded.items():
                if key not in settings:
                    continue
                default = DEFAULT_APP_SETTINGS[key]
                if isinstance(default, bool):
                    settings[key] = bool(value)
                elif isinstance(default, str):
                    settings[key] = value if isinstance(value, str) else default
                else:
                    settings[key] = value
        except Exception:
            pass
    return settings


def _coerce_setting_for_save(key, default, value):
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, str):
        return value if isinstance(value, str) else default
    return value


def write_app_settings(settings):
    data = {
        key: _coerce_setting_for_save(key, default, settings.get(key, default))
        for key, default in DEFAULT_APP_SETTINGS.items()
    }
    with open(APP_SETTINGS_CONFIG, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def load_photo_library(path=PHOTO_LIBRARY_MANIFEST):
    """Load stored photo descriptions, tolerating a missing or damaged file."""
    if not os.path.exists(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        photos = data.get("photos", [])
        return [entry for entry in photos if isinstance(entry, dict)]
    except (OSError, ValueError, TypeError):
        logger.exception("Could not read photo library: %s", path)
        return []


def save_photo_library(entries, path=PHOTO_LIBRARY_MANIFEST):
    """Atomically save the portable photo-description library."""
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temporary = path + ".tmp"
    with open(temporary, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "photos": entries}, f, indent=2, ensure_ascii=False)
    os.replace(temporary, path)


def stored_photo_path(photo_path):
    """Prefer a portable relative path for photos located under DATA_DIR."""
    absolute = os.path.abspath(photo_path)
    try:
        relative = os.path.relpath(absolute, DATA_DIR)
        if relative != os.pardir and not relative.startswith(os.pardir + os.sep):
            return relative.replace(os.sep, "/"), "relative"
    except ValueError:
        pass
    return absolute, "absolute"


def resolve_stored_photo_path(entry):
    path = entry.get("path", "")
    if entry.get("pathType") == "relative":
        return os.path.abspath(os.path.join(DATA_DIR, *path.split("/")))
    return os.path.abspath(path) if path else ""


def add_photo_description(photo_path, description, path=PHOTO_LIBRARY_MANIFEST):
    entries = load_photo_library(path)
    stored_path, path_type = stored_photo_path(photo_path)
    entries.append(
        {
            "id": str(uuid.uuid4()),
            "path": stored_path,
            "pathType": path_type,
            "description": description,
            "createdAt": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        }
    )
    save_photo_library(entries, path)
    return entries[-1]


def update_photo_library_path(old_path, new_path, path=PHOTO_LIBRARY_MANIFEST):
    """Point the newest matching entry at a photo's final saved location."""
    entries = load_photo_library(path)
    old_absolute = os.path.normcase(os.path.abspath(old_path))
    replacement, path_type = stored_photo_path(new_path)
    for entry in reversed(entries):
        resolved = resolve_stored_photo_path(entry)
        if resolved and os.path.normcase(os.path.abspath(resolved)) == old_absolute:
            entry["path"] = replacement
            entry["pathType"] = path_type
            save_photo_library(entries, path)
            return True
    return False


def _limit_llama_command(command):
    """Apply conservative defaults to ScanBox's bundled llama.cpp runtime."""
    command = re.sub(
        r"(?i)(?:--threads|-t)\s+\d+",
        f"--threads {VISION_THREADS}",
        command,
    )
    if re.search(r"(?i)--threads-batch(?:\s|=)", command):
        command = re.sub(
            r"(?i)--threads-batch(?:\s+|=)\d+",
            f"--threads-batch {VISION_THREADS}",
            command,
        )
    else:
        command += f" --threads-batch {VISION_THREADS}"
    command = re.sub(
        r"(?i)(?:-n|--n-predict)\s+\d+",
        "-n 384",
        command,
    )
    if re.search(r"(?i)(?:-c|--ctx-size)(?:\s|=)", command):
        command = re.sub(
            r"(?i)(?:-c|--ctx-size)(?:\s+|=)\d+",
            "-c 4096",
            command,
        )
    else:
        command += " -c 4096"
    if re.search(r"(?i)--temp(?:\s|=)", command):
        command = re.sub(r"(?i)--temp(?:\s+|=)[\d.]+", "--temp 0.3", command)
    else:
        command += " --temp 0.3"
    if re.search(r"(?i)--repeat-penalty(?:\s|=)", command):
        command = re.sub(
            r"(?i)--repeat-penalty(?:\s+|=)[\d.]+", "--repeat-penalty 1.1", command
        )
    else:
        command += " --repeat-penalty 1.1"
    if re.search(r"(?i)--repeat-last-n(?:\s|=)", command):
        command = re.sub(
            r"(?i)--repeat-last-n(?:\s+|=)\d+", "--repeat-last-n 64", command
        )
    else:
        command += " --repeat-last-n 64"
    return command


def _trim_repetitive_model_output(output):
    """Stop obvious generation loops while retaining the useful first result.

    Catches both plain repeated lines (AAAA) and short alternating/cyclic
    loops (ABABAB, ABCABCABC) that small vision models sometimes fall into,
    e.g. a receipt transcription that keeps bouncing between two timestamps.
    """
    cleaned = []
    history = []  # normalized, non-empty lines seen so far, in order
    for line in output.splitlines():
        normalized = line.strip().casefold()
        if normalized:
            history.append(normalized)
            for period in (1, 2, 3, 4):
                needed = period * 3
                if (
                    len(history) >= needed
                    and history[-period:] == history[-2 * period : -period]
                    and history[-2 * period : -period]
                    == history[-3 * period : -2 * period]
                ):
                    cleaned.append("[Repeated local AI output stopped.]")
                    return "\n".join(cleaned).strip()
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def _trim_repetitive_phrases(text):
    """Second pass: catches hallucinated loops that repeat a short phrase
    within a single line rather than across separate lines, e.g. a
    headstone reading "Loved by all of us, Loved by all of us, ..." fifty
    times over, comma-separated rather than newline-separated. The
    line-level pass above can't see this because it never sees a repeated
    *line* - it's all one line.

    Only phrases of 3+ words qualify, deliberately excluding common 2-word
    idioms ("on and on", "again and again", "round and round") that are
    legitimate English, not model loops.
    """
    words = text.split(" ")
    if len(words) < 9:
        return text
    kept = []
    for word in words:
        kept.append(word)
        for period in range(3, 9):
            needed = period * 3
            if (
                len(kept) >= needed
                and kept[-period:] == kept[-2 * period : -period]
                and kept[-2 * period : -period] == kept[-3 * period : -2 * period]
            ):
                surviving = kept[: len(kept) - 2 * period]
                return (
                    " ".join(surviving).rstrip(" ,.;:")
                    + " [Repeated local AI output stopped.]"
                )
    return text


# Words legitimately doubled in English idioms ("on and on", "again and
# again", "round and round", "back and forth" - handled separately since the
# words differ). Never trimmed even when the pattern below matches.
_DUPLICATE_WORD_IDIOM_WHITELIST = {"on", "again", "round", "over"}

_duplicate_word_pair_re = re.compile(
    r"\b(\w+)( (?:and|or) )\1\b(?=[\s.,;:!?]|$)", re.IGNORECASE
)


def _trim_duplicate_word_pairs(text):
    """Catches a narrower stutter than the loop-detectors above: greedy
    decoding sometimes lands on the same word twice in a row across a short
    gap, e.g. "the overall mood is somber and somber" - not a repeated
    n-gram (the surrounding words differ each time, so no_repeat_ngram_size
    doesn't block it), just the same word chosen again a couple of tokens
    later. Collapses "X and X" / "X or X" down to a single "X", except for
    idioms where doubling is normal English.
    """

    def _replace(match):
        word = match.group(1)
        if word.lower() in _DUPLICATE_WORD_IDIOM_WHITELIST:
            return match.group(0)
        return word

    return _duplicate_word_pair_re.sub(_replace, text)


# --- Florence-2 (ONNX Runtime) vision engine -------------------------------
# A much smaller alternative to the llama.cpp GGUF models above (roughly
# 250-300MB installed vs 1.4-2.5GB), for people who want local photo
# description and OCR but don't want the bigger download. It's a genuinely
# different runtime, not another llama.cpp command: ONNX Runtime running
# in-process rather than a subprocess CLI, since Florence-2's architecture
# isn't supported by llama.cpp. This code was written against Florence-2's
# documented ONNX export conventions and a working third-party reference
# implementation, but could not be run against the real model weights in
# development - the environment this was built in blocks bulk downloads
# from huggingface.co. Treat it as reviewed-but-unexercised code.
#
# If both a Florence-2 pack and a llama.cpp pack are installed at once,
# Florence-2 takes priority (see vision_ready/run_vision_task below), on
# the assumption that if someone went and installed the smaller pack, they
# want it used.

FLORENCE_IMAGE_SIZE = 768
FLORENCE_IMAGE_MEAN = (0.485, 0.456, 0.406)
FLORENCE_IMAGE_STD = (0.229, 0.224, 0.225)
# NOT hardcoded to a fixed layer count: Florence-2-base has 6 decoder
# layers, Florence-2-large has 12. run_florence_task figures this out from
# the actual KV-cache tensors the decoder returns on its first pass, so
# either pack (or any future variant) works without a code change here.
FLORENCE_MAX_NEW_TOKENS = 512
FLORENCE_EOS_TOKEN_ID = 2
# Florence-2's task prompts are fixed strings baked into the model itself -
# there's no free-form instruction prompt like DESCRIBE_PROMPT/
# TRANSCRIBE_PROMPT, and no way to splice in OCR-draft grounding text.
FLORENCE_TASK_PROMPTS = {
    "describe": "Describe with a paragraph what is shown in the image.",
    "transcribe": "What is the text in the image?",
}
# Florence-2's own generation_config.json specifies this (alongside beam
# search, which greedy decoding here doesn't use). Without it, greedy
# decoding is prone to short immediate stutters like "rectangular
# rectangular frame" or "a cemetery or a cemetery" - a single word or short
# phrase repeated exactly once, right next to itself. That's a different,
# milder failure mode than the multi-repeat loops _trim_repetitive_phrases
# catches, and blocking it during generation is more reliable than trying
# to clean it up after the fact.
FLORENCE_NO_REPEAT_NGRAM_SIZE = 3

_florence_cache = {}


def _pick_next_token_no_repeat(logits_row, generated_tokens, ngram_size):
    """Greedy next-token pick that refuses to complete an n-gram already
    generated, mirroring HuggingFace's no_repeat_ngram_size logits
    processor. logits_row is shape (1, vocab); generated_tokens is the
    plain-Python list of token ids so far (not yet including this pick)."""
    logits_row = np.array(logits_row, copy=True)
    if ngram_size > 0 and len(generated_tokens) >= ngram_size - 1:
        prefix = tuple(generated_tokens[-(ngram_size - 1):]) if ngram_size > 1 else ()
        banned = set()
        for i in range(len(generated_tokens) - ngram_size + 1):
            if tuple(generated_tokens[i : i + ngram_size - 1]) == prefix:
                banned.add(generated_tokens[i + ngram_size - 1])
        for token_id in banned:
            logits_row[0, token_id] = -np.inf
    return int(np.argmax(logits_row, axis=-1)[0])


_FLORENCE_FILE_PATTERNS = {
    "vision_encoder": lambda n: n.startswith("vision_encoder") and n.endswith(".onnx"),
    "embed_tokens": lambda n: n.startswith("embed_tokens") and n.endswith(".onnx"),
    "encoder_model": lambda n: n.startswith("encoder_model") and n.endswith(".onnx"),
    "decoder_model": lambda n: (
        n.startswith("decoder_model") and "merged" not in n and n.endswith(".onnx")
    ),
    "decoder_model_merged": lambda n: n.startswith("decoder_model_merged") and n.endswith(".onnx"),
    "tokenizer": lambda n: n == "tokenizer.json",
}


def _find_florence_files_in(dirpath):
    """Look for the 5 Florence-2 ONNX pieces plus tokenizer.json directly
    inside dirpath (not recursively - each pack's files sit flat in their own
    folder). Returns a dict of paths, or None if anything is missing."""
    if not os.path.isdir(dirpath):
        return None
    found = {}
    try:
        names = os.listdir(dirpath)
    except OSError:
        return None
    for name in names:
        full = os.path.join(dirpath, name)
        if not os.path.isfile(full):
            continue
        low = name.lower()
        for key, predicate in _FLORENCE_FILE_PATTERNS.items():
            if key not in found and predicate(low):
                found[key] = full
    if len(found) != len(_FLORENCE_FILE_PATTERNS):
        return None
    return found


# Florence-2-large's vision_encoder_int8.onnx is about 366MB; base's is well
# under half that. Used only to sort out a pre-existing flat install left
# over from before the base/large subdirectory split - never to distinguish
# a fresh install, which always writes to a size-specific subdirectory.
_FLORENCE_LARGE_ENCODER_MIN_BYTES = 200 * 1024 * 1024
_florence_migration_done = False


def _migrate_legacy_flat_florence_install():
    """One-time cleanup: ScanBox used to install Florence-2 straight into
    engines\\vision with no size subfolder. If that's what's sitting there,
    figure out which size it actually is (by the vision encoder's file size)
    and move it into the matching florence2-base/florence2-large
    subdirectory, so it's labeled correctly in Settings and won't collide
    with an install of the other size later. Never overwrites an existing
    subdirectory install. Safe to call repeatedly - a no-op once migrated."""
    found = _find_florence_files_in(VISION_DIR)
    if not found:
        return
    try:
        encoder_size = os.path.getsize(found["vision_encoder"])
    except OSError:
        return
    size = "large" if encoder_size >= _FLORENCE_LARGE_ENCODER_MIN_BYTES else "base"
    target_dir = os.path.join(VISION_DIR, FLORENCE_SIZE_SUBDIRS[size])
    if _find_florence_files_in(target_dir):
        # Something already installed there - leave the flat files alone
        # rather than risk clobbering a working install.
        return
    try:
        os.makedirs(target_dir, exist_ok=True)
        for path in found.values():
            os.replace(path, os.path.join(target_dir, os.path.basename(path)))
        logger.info("Migrated flat Florence-2 install to %s (detected as %s)", target_dir, size)
    except OSError:
        logger.exception("Could not migrate legacy flat Florence-2 install")


def _find_florence_files(size):
    """Locate a Florence-2 pack of the given size ("base" or "large").
    Looks in that size's own subdirectory first (engines\\vision\\
    florence2-base or florence2-large), so both sizes can be installed at
    once without one overwriting the other. "base" additionally falls back
    to the flat engines\\vision folder, in case migration above couldn't run
    (e.g. a read-only install folder)."""
    global _florence_migration_done
    if not _florence_migration_done:
        _florence_migration_done = True
        try:
            _migrate_legacy_flat_florence_install()
        except Exception:
            logger.exception("Florence-2 legacy install migration failed")

    subdir = FLORENCE_SIZE_SUBDIRS.get(size, "")
    search_dirs = []
    if subdir:
        search_dirs.extend(os.path.join(base, subdir) for base in _vision_search_dirs())
    if size == "base":
        search_dirs.extend(_vision_search_dirs())
    for dirpath in search_dirs:
        found = _find_florence_files_in(dirpath)
        if found:
            return found
    return None


def _current_florence_size():
    return read_app_settings().get("florence_model_size", "base")


def _get_florence_engine(size=None):
    """Lazily load and cache the ONNX sessions + tokenizer for one Florence-2
    size, once per app run. Unlike the llama.cpp path (a fresh subprocess and
    model load on every single call), this runs in-process, so caching
    actually saves real time across multiple photos. Cached per size so
    switching the Settings selector doesn't require restarting ScanBox."""
    size = size or _current_florence_size()
    if size in _florence_cache:
        return _florence_cache[size]

    engine = None
    if ort is not None and _HFTokenizer is not None and np is not None:
        files = _find_florence_files(size)
        if files:
            try:
                session_options = ort.SessionOptions()
                session_options.intra_op_num_threads = VISION_THREADS
                providers = ["CPUExecutionProvider"]
                sessions = {
                    key: ort.InferenceSession(path, sess_options=session_options, providers=providers)
                    for key, path in files.items()
                    if key != "tokenizer"
                }
                tokenizer = _HFTokenizer.from_file(files["tokenizer"])
                engine = {"sessions": sessions, "tokenizer": tokenizer}
            except Exception:
                logger.exception("Could not load Florence-2 ONNX engine (%s)", size)
                engine = None

    _florence_cache[size] = engine
    return engine


def florence_ready(size=None):
    return _get_florence_engine(size) is not None


def _florence_preprocess_image(image_path):
    with Image.open(image_path) as img:
        img = ImageOps.exif_transpose(img).convert("RGB")
        img = img.resize((FLORENCE_IMAGE_SIZE, FLORENCE_IMAGE_SIZE), Image.BICUBIC)
        arr = np.asarray(img).astype(np.float32) / 255.0
        mean = np.array(FLORENCE_IMAGE_MEAN, dtype=np.float32)
        std = np.array(FLORENCE_IMAGE_STD, dtype=np.float32)
        arr = (arr - mean) / std
        arr = arr.transpose(2, 0, 1)  # HWC -> CHW
        return arr[np.newaxis, ...].astype(np.float32)


def run_florence_task(task, image_path, size=None):
    """Describe or transcribe an image using a locally installed Florence-2
    ONNX pack. Returns plain text on success, or a string starting with
    "Local vision" on failure - the same convention run_vision_task uses,
    so callers don't need to know which engine answered."""
    engine = _get_florence_engine(size)
    if engine is None:
        return "Local vision is not configured. No Florence-2 pack is installed."

    prompt_text = FLORENCE_TASK_PROMPTS.get(task, FLORENCE_TASK_PROMPTS["describe"])
    sessions = engine["sessions"]
    tokenizer = engine["tokenizer"]

    try:
        pixel_values = _florence_preprocess_image(image_path)
        input_ids = np.array([tokenizer.encode(prompt_text).ids], dtype=np.int64)

        image_features = sessions["vision_encoder"].run(None, {"pixel_values": pixel_values})[0]
        prompt_embeds = sessions["embed_tokens"].run(None, {"input_ids": input_ids})[0]

        batch_size, image_token_length = image_features.shape[:2]
        image_attention_mask = np.ones((batch_size, image_token_length), dtype=np.int64)
        prompt_attention_mask = np.ones((batch_size, prompt_embeds.shape[1]), dtype=np.int64)

        inputs_embeds = np.concatenate([image_features, prompt_embeds], axis=1)
        attention_mask = np.concatenate([image_attention_mask, prompt_attention_mask], axis=1)

        encoder_hidden_states = sessions["encoder_model"].run(
            None, {"inputs_embeds": inputs_embeds, "attention_mask": attention_mask}
        )[0]

        # First decoder pass: no KV cache yet.
        decoder_outs = sessions["decoder_model"].run(
            None,
            {
                "inputs_embeds": inputs_embeds[:, -1:],
                "encoder_hidden_states": encoder_hidden_states,
                "encoder_attention_mask": attention_mask,
            },
        )
        # Cross-attention (encoder) K/V are fixed for the whole generation,
        # computed once here. Self-attention (decoder) K/V grow every step,
        # re-read fresh from decoder_outs each iteration below.
        encoder_kv = decoder_outs[1:]
        # 4 tensors per layer (decoder key/value, encoder key/value). Works
        # out to 6 for Florence-2-base, 12 for Florence-2-large - whichever
        # is actually installed, no hardcoded assumption needed.
        num_layers = len(encoder_kv) // 4

        generated_tokens = []
        for _ in range(FLORENCE_MAX_NEW_TOKENS):
            logits = decoder_outs[0]
            decoder_kv = decoder_outs[1:]
            next_token = _pick_next_token_no_repeat(
                logits[:, -1, :], generated_tokens, FLORENCE_NO_REPEAT_NGRAM_SIZE
            )
            if next_token == FLORENCE_EOS_TOKEN_ID:
                break
            generated_tokens.append(next_token)

            next_embeds = sessions["embed_tokens"].run(
                None, {"input_ids": np.array([[next_token]], dtype=np.int64)}
            )[0]

            feed = {
                "use_cache_branch": np.array([True], dtype=np.bool_),
                "inputs_embeds": next_embeds,
                "encoder_hidden_states": encoder_hidden_states,
                "encoder_attention_mask": attention_mask,
            }
            for layer in range(num_layers):
                base = layer * 4
                feed[f"past_key_values.{layer}.decoder.key"] = decoder_kv[base]
                feed[f"past_key_values.{layer}.decoder.value"] = decoder_kv[base + 1]
                feed[f"past_key_values.{layer}.encoder.key"] = encoder_kv[base + 2]
                feed[f"past_key_values.{layer}.encoder.value"] = encoder_kv[base + 3]
            decoder_outs = sessions["decoder_model_merged"].run(None, feed)

        text = tokenizer.decode(generated_tokens, skip_special_tokens=False)
        text = text.replace("<s>", "").replace("</s>", "").strip()
        return _trim_duplicate_word_pairs(
            _trim_repetitive_phrases(_trim_repetitive_model_output(text))
        )
    except Exception as exc:
        logger.exception("Florence-2 inference failed for %s", image_path)
        return f"Local vision command could not run: {exc}"


def vision_ready(task="describe"):
    if florence_ready():
        return True
    command = vision_command_for_task(task)
    if not command:
        return False

    first = re.match(r'^\s*(?:"([^"]+)"|(\S+))', command)
    executable = (first.group(1) or first.group(2)) if first else ""
    if not executable or (
        not os.path.exists(executable) and shutil.which(executable) is None
    ):
        logger.warning("Local AI executable is missing: %s", executable)
        return False

    for match in re.finditer(
        r'(?i)(?:-m|--model|--mmproj)\s+(?:"([^"]+)"|(\S+))',
        command,
    ):
        required_path = match.group(1) or match.group(2)
        if not os.path.exists(required_path):
            logger.warning("Local AI model component is missing: %s", required_path)
            return False
    return True


def vision_command_for_task(task):
    """Prefer the matching bundled model and reject the other bundled role."""
    command = build_vision_command(task)
    if command:
        return command
    command = read_vision_command()
    lower = command.lower()
    if task == "transcribe" and "smolvlm-500m" in lower:
        return ""
    if task == "describe" and "lfm2-vl-450m" in lower:
        return ""
    return command


def load_vision_pack(size="base"):
    """Return the install manifest for the given Florence-2 size ("base" or
    "large"). A user-supplied vision_pack.json (in the writable config dir,
    or shipped alongside the app) is an advanced override that replaces the
    built-in pack entirely, regardless of size - copy vision_pack.example.json
    there to use it. Otherwise, the built-in per-size manifest shipped in
    config\\ is used."""
    for path in (VISION_PACK_MANIFEST, os.path.join(BASE, "config", "vision_pack.json")):
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    builtin_name = FLORENCE_MANIFEST_FILENAMES.get(size, FLORENCE_MANIFEST_FILENAMES["base"])
    builtin_path = os.path.join(BASE, "config", builtin_name)
    if os.path.exists(builtin_path):
        with open(builtin_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return DEFAULT_VISION_PACK


def _progress_hook(name, status_callback):
    """Feed each percentage change to the native progress control."""
    state = {"last": -1}

    def hook(block_num, block_size, total_size):
        if not status_callback or total_size <= 0:
            return
        downloaded = block_num * block_size
        pct = int(min(100, downloaded * 100 / total_size))
        if pct != state["last"]:
            mb = total_size / (1024 * 1024)
            status_callback(f"{name}: {pct}% of {mb:.0f} MB")
            state["last"] = pct

    return hook


def _vision_search_dirs():
    dirs = [VISION_DIR]
    if BUNDLED_VISION_DIR != VISION_DIR and os.path.isdir(BUNDLED_VISION_DIR):
        dirs.append(BUNDLED_VISION_DIR)
    return dirs


def _find_in_vision(predicate):
    for base in _vision_search_dirs():
        for dirpath, _dirs, names in os.walk(base):
            for name in names:
                if predicate(name):
                    return os.path.join(dirpath, name)
    return None


def _find_model_gguf(name_fragment):
    for base in _vision_search_dirs():
        for dirpath, _dirs, names in os.walk(base):
            for name in names:
                low = name.lower()
                if (
                    low.endswith(".gguf")
                    and not low.startswith("mmproj")
                    and name_fragment.lower() in low
                ):
                    return os.path.join(dirpath, name)
    return None


def _matching_mmproj(model):
    """Select the projector belonging to the chosen model, not another pack."""
    candidates = []
    model_name = os.path.basename(model).lower()
    model_tokens = {
        token
        for token in re.split(r"[^a-z0-9]+", model_name)
        if len(token) >= 3 and token not in {"gguf", "instruct"}
    }
    for base in _vision_search_dirs():
        for dirpath, _dirs, names in os.walk(base):
            for name in names:
                low = name.lower()
                if not (low.startswith("mmproj") and low.endswith(".gguf")):
                    continue
                score = sum(token in low for token in model_tokens)
                candidates.append((score, os.path.getsize(os.path.join(dirpath, name)), os.path.join(dirpath, name)))
    if not candidates:
        return None
    return max(candidates)[2]


def safe_extract_zip(archive, destination):
    destination = os.path.abspath(destination)
    for member in archive.infolist():
        target = os.path.abspath(os.path.join(destination, member.filename))
        if os.path.commonpath([destination, target]) != destination:
            raise ValueError(f"Unsafe path in zip file: {member.filename}")
    archive.extractall(destination)


def build_vision_command(task="describe"):
    """Locate the runtime, model, and projector that were just installed and
    assemble a llama-mtmd-cli command. Works regardless of how the runtime zip
    laid out its folders."""
    runtime = _find_in_vision(lambda n: n.lower() == "llama-mtmd-cli.exe")
    # Qwen3-VL-2B is one model that handles both photo description and
    # document transcription. Fall back to the older per-task SmolVLM/
    # LFM2-VL pair if that's what's actually installed.
    fallback_family = "lfm2-vl-450m" if task == "transcribe" else "smolvlm-500m"
    model = _find_model_gguf("qwen3vl-2b") or _find_model_gguf(fallback_family)
    mmproj = _matching_mmproj(model) if model else None
    if not runtime or not model:
        return ""

    command = '"%s" -m "%s"' % (runtime, model)
    if mmproj:
        command += ' --mmproj "%s"' % mmproj
    command += (
        f' --threads {VISION_THREADS} --threads-batch {VISION_THREADS} '
        '-c 4096 -n 384 --temp 0.3 --repeat-penalty 1.1 --repeat-last-n 64 '
        '--image "{image}" -p "{prompt}"'
    )
    return command


def install_local_ai_pack(status_callback=None, size="base"):
    manifest = load_vision_pack(size)
    files = manifest.get("files", [])
    if not files:
        return "The local AI pack lists no files to download."

    # Florence-2 packs declare "subdir" so base and large install side by
    # side instead of overwriting each other's identically-named files. A
    # custom override manifest (or the old llama.cpp pack) has no "subdir"
    # and installs flat into VISION_DIR exactly as before.
    subdir = manifest.get("subdir", "")
    target_dir = os.path.join(VISION_DIR, subdir) if subdir else VISION_DIR
    os.makedirs(target_dir, exist_ok=True)
    downloads_dir = os.path.join(TEMP_DIR, "downloads")
    os.makedirs(downloads_dir, exist_ok=True)

    for item in files:
        platform_tag = item.get("platform")
        if platform_tag and not platform_tag.startswith("Windows"):
            continue

        url = item.get("url", "").strip()
        name = item.get("name", "").strip()
        if not url or not name:
            return "A local AI download entry is missing a URL or filename."
        if "example.com" in url:
            return (
                f"The download URL for {name} is still a placeholder. Edit "
                "config\\vision_pack.json with a real URL, or delete that file "
                "to use the built-in pack."
            )

        installed_path = os.path.join(target_dir, name)
        if not item.get("extract", False) and os.path.exists(installed_path):
            if status_callback:
                status_callback(f"{name} is already installed.")
            continue
        if (
            (item.get("extract", False) or name.lower().endswith(".zip"))
            and _find_in_vision(lambda n: n.lower() == "llama-mtmd-cli.exe")
        ):
            if status_callback:
                status_callback("Local AI runtime is already installed.")
            continue

        if status_callback:
            status_callback(f"Downloading {name}...")
        download_path = os.path.join(downloads_dir, name)
        try:
            urllib.request.urlretrieve(
                url, download_path, _progress_hook(name, status_callback)
            )
        except Exception as exc:
            return f"Download failed for {name}: {exc}"

        if item.get("extract", False) or name.lower().endswith(".zip"):
            if status_callback:
                status_callback(f"Extracting {name}...")
            if not name.lower().endswith(".zip"):
                return f"Do not know how to extract {name}."
            # The llama.cpp runtime archive is the only thing ever extracted,
            # and it always installs flat regardless of any manifest subdir.
            with zipfile.ZipFile(download_path, "r") as archive:
                safe_extract_zip(archive, VISION_DIR)
            os.remove(download_path)
        else:
            os.replace(download_path, installed_path)

    # A Florence-2 pack has no shell command to write - it runs in-process
    # through ONNX Runtime instead. Clear the cached "not ready" result from
    # any earlier check this session, then see if it's actually usable now.
    _florence_cache.pop(size, None)
    if florence_ready(size):
        size_label = "smaller" if size == "base" else "larger"
        return (
            f"Local AI pack installed and configured. ScanBox will use the "
            f"{size_label} Florence-2 model for both photographs and "
            "document/receipt transcription."
        )

    command = manifest.get("command", "").strip() or build_vision_command("describe")
    document_command = build_vision_command("transcribe")
    if not command or not document_command:
        return (
            "Files downloaded, but ScanBox could not find the runtime and both models "
            f"in {VISION_DIR}. Use Local AI Setup to enter the command manually."
        )

    write_vision_command(command)
    return (
        "Local AI pack installed and configured. ScanBox will use Qwen3-VL-2B "
        "for both photographs and document/receipt transcription."
    )


def _run_vision_prompt(task, image_path, prompt, token_limit=384, draft_path=""):
    """Low-level: run the configured local vision model on image_path with an
    explicit prompt. Returns (output, error) where exactly one is set. Used
    by run_vision_task for full descriptions/transcriptions."""
    command_text = vision_command_for_task(task)
    if not command_text:
        return None, (
            "Local vision is not configured. Add a command in "
            f"{VISION_CONFIG}, use Local AI Setup, or install a local AI pack."
        )

    try:
        started = time.perf_counter()
        logger.info(
            "Starting local vision task=%s image=%s exists=%s",
            task,
            image_path,
            os.path.exists(image_path),
        )
        command = command_text.format(
            task=task,
            image=image_path,
            prompt=prompt,
            draft=draft_path,
        )
        if "{image}" not in command_text:
            command = f'{command} --task "{task}" --image "{image_path}"'
            if draft_path:
                command += f' --draft "{draft_path}"'
        if "llama-mtmd-cli" in command_text.lower():
            command = re.sub(
                r"(?i)(?:-n|--n-predict)\s+\d+",
                f"-n {token_limit}",
                command,
            )
        ai_env = os.environ.copy()
        ai_env["OMP_NUM_THREADS"] = str(VISION_THREADS)
        ai_env["OMP_THREAD_LIMIT"] = str(VISION_THREADS)
        result = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env=ai_env,
            **AI_PROCESS_FLAGS,
        )
        output = _trim_duplicate_word_pairs(
            _trim_repetitive_phrases(_trim_repetitive_model_output(result.stdout.strip()))
        )
        logger.debug(
            "Local vision finished returncode=%s elapsed=%.2fs\nstdout:\n%s\nstderr:\n%s",
            result.returncode,
            time.perf_counter() - started,
            output[:VISION_LOG_LIMIT],
            result.stderr.strip()[:VISION_LOG_LIMIT],
        )
        if result.returncode != 0:
            error = (
                result.stderr.strip()
                or output
                or f"No error details returned (exit code {result.returncode})."
            )
            return None, f"Local vision command failed: {error}"
        return (output or "Local vision returned no text."), None
    except subprocess.TimeoutExpired:
        logger.exception("Local vision command timed out for %s", image_path)
        return None, "Local vision command timed out."
    except Exception as exc:
        logger.exception("Local vision command could not run for %s", image_path)
        return None, f"Local vision command could not run: {exc}"


def run_vision_task(task, image_path, ocr_draft="", prompt_override=None):
    if florence_ready():
        return run_florence_task(task, image_path)

    prompt = (
        prompt_override
        if task == "describe" and prompt_override
        else TRANSCRIBE_PROMPT if task == "transcribe" else DESCRIBE_PROMPT
    )
    if task == "describe" and ocr_draft.strip():
        trimmed_draft = ocr_draft.strip()[:OCR_DRAFT_MAX_CHARS]
        prompt += (
            "\n\nOCR independently detected the following visible text. Use it "
            "to copy words and names accurately, but do not mention this OCR "
            "instruction in the answer:\n" + trimmed_draft
        )
    draft_path = ""
    if ocr_draft:
        draft_path = os.path.join(TEMP_DIR, f"ocr_draft_{uuid.uuid4().hex}.txt")
        with open(draft_path, "w", encoding="utf-8") as f:
            f.write(ocr_draft)

    try:
        output, error = _run_vision_prompt(
            task, image_path, prompt, token_limit=384, draft_path=draft_path
        )
        return error or output
    finally:
        if draft_path and os.path.exists(draft_path):
            try:
                os.remove(draft_path)
            except OSError:
                pass


PAGE_CROP_MIN_AREA_RATIO = 0.15
PAGE_CROP_MAX_AREA_RATIO = 0.97
PAGE_CROP_MAX_DIMENSION = 1000


def _order_page_points(pts):
    """Sort four (x, y) points into top-left, top-right, bottom-right,
    bottom-left order, regardless of the order the contour returned them."""
    rect = np.zeros((4, 2), dtype="float32")
    total = pts.sum(axis=1)
    rect[0] = pts[np.argmin(total)]
    rect[2] = pts[np.argmax(total)]
    diff = np.diff(pts, axis=1)
    rect[1] = pts[np.argmin(diff)]
    rect[3] = pts[np.argmax(diff)]
    return rect


def detect_and_crop_page(image_path):
    """Find a page- or photo-sized rectangle in a camera capture (an
    overhead document camera, or a handheld/desktop scanner such as a
    Pearl/IRIScan-style device that photographs the page rather than
    scanning it through a calibrated driver) and perspective-correct +
    crop it, discarding the desk/background around it.

    Safe no-op on flatbed/WIA scans (already edge-to-edge, so no separate
    page contour exists) and on ordinary photos with no single dominant
    rectangular subject. Returns True if the image was cropped in place,
    False if left unchanged for any reason (including OpenCV not being
    available)."""
    if cv2 is None or np is None:
        return False
    try:
        with Image.open(image_path) as img:
            img = ImageOps.exif_transpose(img).convert("RGB")
            original = np.array(img)
    except Exception:
        logger.exception("Could not open %s for page detection", image_path)
        return False

    height, width = original.shape[:2]
    if height < 50 or width < 50:
        return False

    scale = min(PAGE_CROP_MAX_DIMENSION / float(max(height, width)), 1.0)
    small = cv2.resize(original, (max(int(width * scale), 1), max(int(height * scale), 1)))

    try:
        gray = cv2.cvtColor(small, cv2.COLOR_RGB2GRAY)
        blurred = cv2.GaussianBlur(gray, (5, 5), 0)
        edges = cv2.dilate(cv2.Canny(blurred, 50, 150), None, iterations=2)
        contours, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    except Exception:
        logger.exception("Page detection edge-finding failed for %s", image_path)
        return False

    if not contours:
        return False

    small_area = small.shape[0] * small.shape[1]
    best_points = None
    best_area = 0
    for contour in sorted(contours, key=cv2.contourArea, reverse=True)[:10]:
        area = cv2.contourArea(contour)
        ratio = area / small_area
        if ratio < PAGE_CROP_MIN_AREA_RATIO or ratio > PAGE_CROP_MAX_AREA_RATIO:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.02 * perimeter, True)
        if len(approx) == 4 and cv2.isContourConvex(approx) and area > best_area:
            best_points = approx.reshape(4, 2).astype("float32")
            best_area = area

    if best_points is None:
        return False

    rect = _order_page_points(best_points / scale)
    (top_left, top_right, bottom_right, bottom_left) = rect
    max_width = int(max(
        np.linalg.norm(bottom_right - bottom_left),
        np.linalg.norm(top_right - top_left),
    ))
    max_height = int(max(
        np.linalg.norm(top_right - bottom_right),
        np.linalg.norm(top_left - bottom_left),
    ))
    if max_width < 50 or max_height < 50:
        return False

    destination = np.array(
        [
            [0, 0],
            [max_width - 1, 0],
            [max_width - 1, max_height - 1],
            [0, max_height - 1],
        ],
        dtype="float32",
    )
    try:
        matrix = cv2.getPerspectiveTransform(rect, destination)
        warped = cv2.warpPerspective(original, matrix, (max_width, max_height))
        save_kwargs = {}
        if os.path.splitext(image_path)[1].lower() in (".jpg", ".jpeg"):
            save_kwargs["quality"] = 95
        Image.fromarray(warped).save(image_path, **save_kwargs)
    except Exception:
        logger.exception("Could not warp/save cropped page for %s", image_path)
        return False

    logger.info(
        "Auto-cropped page in %s to %sx%s (was %sx%s)",
        image_path,
        max_width,
        max_height,
        width,
        height,
    )
    return True


def detect_and_crop_screen_document(image_path):
    """Conservatively isolate a document displayed inside an application."""
    if detect_and_crop_page(image_path):
        logger.info("Screen OCR isolated document with page contour detection")
        return True
    if cv2 is None or np is None:
        return False

    try:
        with Image.open(image_path) as img:
            rgb = np.array(ImageOps.exif_transpose(img).convert("RGB"))
    except Exception:
        logger.exception("Could not open %s for screen document detection", image_path)
        return False

    height, width = rgb.shape[:2]
    if height < 100 or width < 100:
        return False

    scale = min(PAGE_CROP_MAX_DIMENSION / float(max(height, width)), 1.0)
    small = cv2.resize(
        rgb,
        (max(int(width * scale), 1), max(int(height * scale), 1)),
    )
    hsv = cv2.cvtColor(small, cv2.COLOR_RGB2HSV)
    # Paper and PDF page backgrounds are normally bright and fairly neutral.
    mask = cv2.inRange(hsv, np.array((0, 0, 150)), np.array((179, 85, 255)))
    kernel_size = max(5, int(min(small.shape[:2]) * 0.018))
    if kernel_size % 2 == 0:
        kernel_size += 1
    kernel = cv2.getStructuringElement(
        cv2.MORPH_RECT, (kernel_size, kernel_size)
    )
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    image_area = float(small.shape[0] * small.shape[1])
    image_center = np.array((small.shape[1] / 2.0, small.shape[0] / 2.0))
    best = None
    best_score = 0.0

    for contour in contours:
        x, y, candidate_width, candidate_height = cv2.boundingRect(contour)
        box_area = candidate_width * candidate_height
        area_ratio = box_area / image_area
        aspect = candidate_width / float(candidate_height)
        if (
            area_ratio < 0.14
            or candidate_height < small.shape[0] * 0.34
            or candidate_width < small.shape[1] * 0.25
            or not 0.42 <= aspect <= 1.9
        ):
            continue

        contour_fill = cv2.contourArea(contour) / float(max(box_area, 1))
        if contour_fill < 0.42:
            continue
        center = np.array((x + candidate_width / 2.0, y + candidate_height / 2.0))
        center_distance = np.linalg.norm(
            (center - image_center)
            / np.array((small.shape[1], small.shape[0]))
        )
        center_score = max(0.0, 1.0 - center_distance * 2.2)
        score = area_ratio * 2.0 + contour_fill + center_score
        if score > best_score:
            best = (x, y, candidate_width, candidate_height)
            best_score = score

    if best is None:
        logger.info("Screen OCR did not find a confident document region")
        return False

    x, y, candidate_width, candidate_height = best
    left = max(0, int(x / scale))
    top = max(0, int(y / scale))
    right = min(width, int((x + candidate_width) / scale))
    bottom = min(height, int((y + candidate_height) / scale))
    if right - left < 100 or bottom - top < 100:
        return False

    Image.fromarray(rgb[top:bottom, left:right]).save(image_path)
    logger.info(
        "Screen OCR cropped document region left=%d top=%d right=%d bottom=%d",
        left,
        top,
        right,
        bottom,
    )
    return True


def preprocess_for_ocr(path):
    clean_path = os.path.splitext(path)[0] + "_clean.png"
    with Image.open(path) as img:
        img = ImageOps.exif_transpose(img)
        img = img.convert("L")
        img = ImageOps.autocontrast(img)
        img = ImageEnhance.Contrast(img).enhance(1.8)
        img = img.filter(ImageFilter.SHARPEN)
        img.save(clean_path, "PNG")
    return clean_path


def detect_orientation(path):
    try:
        result = subprocess.run(
            [TESS, path, "stdout", "--psm", "0"],
            capture_output=True,
            text=True,
            check=True,
            **NO_WINDOW,
        )
        rotation = 0
        for line in result.stdout.splitlines():
            if line.strip().startswith("Rotate:"):
                rotation = int(line.split()[-1])
                break

        if rotation % 360 == 0:
            return "Page upright"

        if rotation in (90, 180, 270):
            with Image.open(path) as img:
                img.rotate(-rotation, expand=True).save(path)
            return f"Page rotated {rotation} degrees. Corrected"

        return f"Page rotated {rotation} degrees"
    except Exception:
        return "Orientation unknown"


def _remove_quietly(*paths):
    for path in paths:
        if path and os.path.exists(path):
            try:
                os.remove(path)
            except OSError:
                pass


def clear_temp_directory():
    """Remove session working files on shutdown, but retain the temp folder."""
    temp_root = os.path.abspath(TEMP_DIR)
    if not os.path.isdir(temp_root):
        return

    removed_files = 0
    for dirpath, dirnames, filenames in os.walk(temp_root, topdown=False):
        # Never follow or traverse directory links during cleanup.
        for dirname in list(dirnames):
            directory = os.path.abspath(os.path.join(dirpath, dirname))
            if os.path.islink(directory):
                try:
                    os.unlink(directory)
                except OSError:
                    pass
                continue
            try:
                if os.path.commonpath([temp_root, directory]) == temp_root:
                    os.rmdir(directory)
            except OSError:
                pass

        for filename in filenames:
            target = os.path.abspath(os.path.join(dirpath, filename))
            try:
                if os.path.commonpath([temp_root, target]) == temp_root:
                    os.remove(target)
                    removed_files += 1
            except OSError:
                pass

    logger.info("Shutdown temp cleanup removed %s files from %s", removed_files, temp_root)


def _parse_ocr_tsv(tsv_path):
    """Reconstruct page text and average word confidence from a Tesseract TSV.
    Words are grouped into lines and paragraphs so a single TSV pass replaces
    the old separate plain-text run."""
    lines = {}
    order = []
    confidences = []
    with open(tsv_path, "r", encoding="utf-8") as f:
        rows = f.readlines()[1:]
    for row in rows:
        parts = row.rstrip("\n").split("\t")
        if len(parts) <= 11:
            continue
        try:
            conf = float(parts[10])
        except ValueError:
            continue
        word = (parts[11] or "").strip()
        if not word:
            continue
        if conf >= 0:
            confidences.append(conf)
        key = (parts[2], parts[3], parts[4])  # block, paragraph, line
        if key not in lines:
            lines[key] = []
            order.append(key)
        lines[key].append(word)

    blocks = []
    prev_par = None
    for key in order:
        par = (key[0], key[1])
        if prev_par is not None and par != prev_par:
            blocks.append("")  # blank line separates paragraphs
        blocks.append(" ".join(lines[key]))
        prev_par = par

    text = "\n".join(blocks).strip()
    avg = sum(confidences) / len(confidences) if confidences else 0
    return text, avg


def ocr_local_with_confidence(path):
    clean_path = None
    tsv_path = None
    try:
        started = time.perf_counter()
        clean_path = preprocess_for_ocr(path)
        temp_base = os.path.splitext(clean_path)[0] + "_conf"
        tsv_path = temp_base + ".tsv"
        subprocess.run(
            [TESS, clean_path, temp_base, "-l", "eng", "--psm", "6", "tsv"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
            **NO_WINDOW,
        )
        if not os.path.exists(tsv_path):
            return "", 0
        text, confidence = _parse_ocr_tsv(tsv_path)
        logger.info(
            "Tesseract OCR finished elapsed=%.2fs confidence=%.1f image=%s",
            time.perf_counter() - started,
            confidence,
            path,
        )
        return text, confidence
    except Exception as exc:
        return f"[Local OCR failed: {exc}]", 0
    finally:
        _remove_quietly(clean_path, tsv_path)


def looks_like_unsupported_handwriting(text, confidence):
    words = [word for word in text.replace("\n", " ").split(" ") if word.strip()]
    return confidence < HANDWRITING_CONF_THRESHOLD and len(words) <= HANDWRITING_WORD_LIMIT


def read_docx_text(path):
    """Extract readable text (paragraphs and tables) from a .docx file so it can
    be shown in the read-only window without opening Word."""
    document = docx.Document(path)
    blocks = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            blocks.append(text)
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                rows.append("\t".join(cells))
        if rows:
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def save_text_output(text, path, fmt):
    if fmt == "txt":
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
    elif fmt == "docx":
        doc = docx.Document()
        for block in text.split("\n\n"):
            doc.add_paragraph(block)
        doc.save(path)
    elif fmt == "pdf":
        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Helvetica", size=12)
        safe = text.encode("latin-1", "replace").decode("latin-1")
        pdf.multi_cell(0, 10, safe)
        pdf.output(path)


class ScanBoxTaskBarIcon(wx.adv.TaskBarIcon):
    """Notification-area access while the main window is hidden."""

    def __init__(self, frame):
        super().__init__()
        self.frame = frame
        self.open_id = wx.NewIdRef()
        self.settings_id = wx.NewIdRef()
        self.exit_id = wx.NewIdRef()
        self.Bind(wx.adv.EVT_TASKBAR_LEFT_DCLICK, self._open)
        self.Bind(wx.EVT_MENU, self._open, id=self.open_id)
        self.Bind(wx.EVT_MENU, self._settings, id=self.settings_id)
        self.Bind(wx.EVT_MENU, self._exit, id=self.exit_id)
        icon = wx.ArtProvider.GetIcon(
            wx.ART_INFORMATION, wx.ART_OTHER, (16, 16)
        )
        self.SetIcon(icon, f"{APP_NAME} {APP_VERSION}")

    def CreatePopupMenu(self):
        menu = wx.Menu()
        menu.Append(self.open_id, "&Open ScanBox\tCtrl+Alt+\\")
        menu.Append(self.settings_id, "&Settings")
        menu.AppendSeparator()
        menu.Append(self.exit_id, "E&xit ScanBox")
        return menu

    def _open(self, event=None):
        self.frame.restore_from_notification_area()

    def _settings(self, event=None):
        self.frame.restore_from_notification_area(self.frame.open_settings)

    def _exit(self, event=None):
        self.frame.Close()


class ScanBox(wx.Frame):
    def __init__(self):
        super().__init__(None, title="ScanBox", size=(940, 780))

        self.session_files = []
        self.session_file_sources = []
        self.session_file_modes = []
        self.session_pages = []
        self.session_page_modes = []
        self.page_counter = 0
        self.installing = False
        self.busy = False
        self.busy_status_visible = False
        self.busy_status_token = 0
        self.app_settings = read_app_settings()
        configure_logging(self.app_settings.get("diagnostic_logging", False))
        self.created_output_files = []
        self.tray_icon = None
        self._restore_focus = None
        self._tray_hide_timer = None
        self._toggle_minimize_timer = None
        self._toggle_minimize_announced = False
        self._restore_timer = None

        menu_bar = wx.MenuBar()
        help_menu = wx.Menu()
        manual_item = help_menu.Append(wx.ID_HELP, "&User Manual\tF1")
        self.check_updates_item = help_menu.Append(wx.ID_ANY, "Check for &Updates")
        help_menu.AppendSeparator()
        about_item = help_menu.Append(wx.ID_ABOUT, "&About ScanBox")
        menu_bar.Append(help_menu, "&Help")
        self.SetMenuBar(menu_bar)
        self.Bind(wx.EVT_MENU, self.open_user_manual, manual_item)
        self.Bind(wx.EVT_MENU, self.check_for_updates, self.check_updates_item)
        self.Bind(wx.EVT_MENU, self.show_about, about_item)

        panel = wx.Panel(self)
        root = wx.BoxSizer(wx.VERTICAL)

        self.mode_tabs = wx.Notebook(panel)
        self.document_panel = wx.Panel(self.mode_tabs)
        self.document_panel.SetAccessible(_NamedPageAccessible(self.document_panel, "Document"))
        document_sizer = wx.BoxSizer(wx.HORIZONTAL)
        self.document_scan_btn = wx.Button(self.document_panel, label="Scan Document")
        self.document_import_btn = wx.Button(self.document_panel, label="Import Document")
        document_sizer.Add(self.document_scan_btn, 0, wx.ALL, 6)
        document_sizer.Add(self.document_import_btn, 0, wx.ALL, 6)
        self.document_panel.SetSizer(document_sizer)
        self.mode_tabs.AddPage(self.document_panel, "Document")

        self.photo_panel = wx.Panel(self.mode_tabs)
        self.photo_panel.SetAccessible(
            _NamedPageAccessible(self.photo_panel, "Photo Description")
        )
        photo_sizer = wx.BoxSizer(wx.VERTICAL)
        photo_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.photo_scan_btn = wx.Button(self.photo_panel, label="Scan Photo")
        self.photo_import_btn = wx.Button(self.photo_panel, label="Import Photo")
        self.photo_batch_import_btn = wx.Button(
            self.photo_panel, label="Batch Import Photos"
        )
        photo_buttons.Add(self.photo_scan_btn, 0, wx.ALL, 6)
        photo_buttons.Add(self.photo_import_btn, 0, wx.ALL, 6)
        photo_buttons.Add(self.photo_batch_import_btn, 0, wx.ALL, 6)
        self.photo_ocr_checkbox = wx.CheckBox(
            self.photo_panel, label="Use OCR for text in photographs"
        )
        self.photo_ocr_checkbox.SetValue(
            self.app_settings.get("photo_ocr_enabled", False)
        )
        self.photo_ocr_checkbox.Bind(wx.EVT_CHECKBOX, self.on_photo_ocr_toggle)
        photo_sizer.Add(photo_buttons, 0, wx.EXPAND)
        photo_sizer.Add(self.photo_ocr_checkbox, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        self.photo_panel.SetSizer(photo_sizer)
        self.mode_tabs.AddPage(self.photo_panel, "Photo Description")

        self.library_panel = wx.Panel(self.mode_tabs)
        self.library_panel.SetAccessible(
            _NamedPageAccessible(self.library_panel, "Photo Library")
        )
        library_sizer = wx.BoxSizer(wx.VERTICAL)
        self.library_list = wx.ListCtrl(
            self.library_panel,
            style=wx.LC_REPORT | wx.LC_SINGLE_SEL,
        )
        self.library_list.EnableCheckBoxes(True)
        # Put the meaningful description first for screen readers. The actual
        # source filename is the final column, after availability status.
        self.library_list.InsertColumn(0, "Description", width=430)
        self.library_list.InsertColumn(1, "Status", width=100)
        self.library_list.InsertColumn(2, "Photo", width=230)
        self.library_description = wx.TextCtrl(
            self.library_panel,
            # Plain multiline read-only edit control, not TE_RICH2. Rich
            # Edit (MSFTEDIT) has a different accessibility layer that does
            # not reliably report restored focus to screen readers after
            # Alt+Tab. The plain control behaves consistently here.
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        self.library_description.SetName("Stored photo description")
        # Floor height so this stays usable with a screen reader even in a
        # restored (non-maximized) window - see also self.Maximize(True)
        # below, which makes maximized the default state on launch.
        self.library_description.SetMinSize((-1, 180))
        library_buttons = wx.BoxSizer(wx.HORIZONTAL)
        self.library_open_btn = wx.Button(self.library_panel, label="Open Photo")
        self.library_folder_btn = wx.Button(
            self.library_panel, label="Open Containing Folder"
        )
        self.library_copy_btn = wx.Button(self.library_panel, label="Copy Path")
        self.library_remove_btn = wx.Button(
            self.library_panel, label="Remove Stored Description"
        )
        self.library_select_all_btn = wx.Button(
            self.library_panel, label="Select All"
        )
        for button in (
            self.library_open_btn,
            self.library_folder_btn,
            self.library_copy_btn,
            self.library_remove_btn,
            self.library_select_all_btn,
        ):
            library_buttons.Add(button, 0, wx.ALL, 3)
        library_sizer.Add(self.library_list, 1, wx.ALL | wx.EXPAND, 6)
        library_sizer.Add(self.library_description, 1, wx.ALL | wx.EXPAND, 6)
        library_sizer.Add(library_buttons, 0, wx.ALL | wx.EXPAND, 3)
        self.library_panel.SetSizer(library_sizer)
        self.mode_tabs.AddPage(self.library_panel, "Photo Library")
        self.mode_tabs.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, self.on_mode_change)

        self.output_box = wx.TextCtrl(
            panel,
            # See the matching accessibility note on
            # self.library_description above: use a plain edit control,
            # rather than TE_RICH2, for reliable restored-focus events.
            style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP,
        )
        self.output_box.SetName("Results")
        # Stay out of the tab order until there is something to read.
        self.output_box.Disable()
        # Floor height so results stay usable with a screen reader even in a
        # restored (non-maximized) window.
        self.output_box.SetMinSize((-1, 260))

        self.save_text_btn = wx.Button(panel, label="Save Text")
        self.save_img_btn = wx.Button(panel, label="Save Images")
        self.discard_btn = wx.Button(panel, label="Discard Session")
        self.cancel_batch_btn = wx.Button(panel, label="Cancel Batch Import")
        self.cancel_batch_btn.Hide()
        self.settings_btn = wx.Button(panel, label="Settings")
        self.install_gauge = wx.Gauge(
            panel,
            range=100,
            style=wx.GA_HORIZONTAL,
        )
        self.install_gauge.SetName("Local AI download progress")
        self.install_gauge.SetToolTip("Local AI model download progress")
        self.install_gauge.Hide()

        self.document_scan_btn.Bind(
            wx.EVT_BUTTON, lambda event: self.on_scan(event, False)
        )
        self.document_import_btn.Bind(
            wx.EVT_BUTTON, lambda event: self.on_import_image(event, False)
        )
        self.photo_scan_btn.Bind(
            wx.EVT_BUTTON, lambda event: self.on_scan(event, True)
        )
        self.photo_import_btn.Bind(
            wx.EVT_BUTTON, lambda event: self.on_import_image(event, True)
        )
        self.photo_batch_import_btn.Bind(wx.EVT_BUTTON, self.batch_import_photos)
        self.library_list.Bind(wx.EVT_LIST_ITEM_SELECTED, self.on_library_selected)
        self.library_list.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self.open_library_photo)
        self.library_list.Bind(wx.EVT_CONTEXT_MENU, self.on_library_context_menu)
        self.library_list.Bind(wx.EVT_CHAR_HOOK, self.on_library_key)
        self.library_open_btn.Bind(wx.EVT_BUTTON, self.open_library_photo)
        self.library_folder_btn.Bind(wx.EVT_BUTTON, self.open_library_folder)
        self.library_copy_btn.Bind(wx.EVT_BUTTON, self.copy_library_path)
        self.library_remove_btn.Bind(wx.EVT_BUTTON, self.remove_library_entry)
        self.library_select_all_btn.Bind(wx.EVT_BUTTON, self.select_all_library_entries)
        self.save_text_btn.Bind(wx.EVT_BUTTON, self.save_text)
        self.save_img_btn.Bind(wx.EVT_BUTTON, self.save_images)
        self.discard_btn.Bind(wx.EVT_BUTTON, self.reset_session)
        self.cancel_batch_btn.Bind(wx.EVT_BUTTON, self.cancel_batch_import)
        self.settings_btn.Bind(wx.EVT_BUTTON, self.open_settings)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_ICONIZE, self.on_iconize)
        self.Bind(wx.EVT_ACTIVATE, self.on_activate)
        self.screen_hotkey_id = wx.NewIdRef()
        if not self.RegisterHotKey(self.screen_hotkey_id, wx.MOD_CONTROL, 0xDC):
            logger.warning("Could not register global Ctrl+\\ hotkey")
        self.Bind(wx.EVT_HOTKEY, self.on_screen_hotkey, id=self.screen_hotkey_id)
        self.screen_ocr_hotkey_id = wx.NewIdRef()
        if not self.RegisterHotKey(
            self.screen_ocr_hotkey_id, wx.MOD_CONTROL | wx.MOD_SHIFT, 0xDC
        ):
            logger.warning("Could not register global Ctrl+Shift+\\ hotkey")
        self.Bind(
            wx.EVT_HOTKEY,
            self.on_screen_ocr_hotkey,
            id=self.screen_ocr_hotkey_id,
        )
        self.toggle_window_hotkey_id = wx.NewIdRef()
        if not self.RegisterHotKey(
            self.toggle_window_hotkey_id,
            wx.MOD_CONTROL | wx.MOD_ALT,
            0xDC,
        ):
            logger.warning("Could not register global Ctrl+Alt+\\ hotkey")
        self.Bind(
            wx.EVT_HOTKEY,
            self.toggle_window_visibility,
            id=self.toggle_window_hotkey_id,
        )
        settings_id = wx.NewIdRef()
        self.Bind(wx.EVT_MENU, self.open_settings, id=settings_id)
        self.SetAcceleratorTable(
            wx.AcceleratorTable([(wx.ACCEL_CTRL, ord(","), settings_id)])
        )

        self.button_sizer = wx.WrapSizer(wx.HORIZONTAL)
        for btn in (
            self.save_text_btn,
            self.save_img_btn,
            self.discard_btn,
            self.cancel_batch_btn,
            self.settings_btn,
        ):
            self.button_sizer.Add(btn, 0, wx.ALL, 3)

        self.mode_tabs_sizer_item = root.Add(
            self.mode_tabs, 0, wx.ALL | wx.EXPAND, 10
        )
        root.Add(self.output_box, 1, wx.ALL | wx.EXPAND, 10)
        root.Add(self.install_gauge, 0, wx.LEFT | wx.RIGHT | wx.EXPAND, 10)
        root.Add(self.button_sizer, 0, wx.ALL | wx.EXPAND, 10)

        # Page count lives in the status bar so it stays out of the tab order.
        self.CreateStatusBar()
        self.SetStatusText("No active session")

        panel.SetSizer(root)
        self.photo_library = []
        self.refresh_photo_library()
        self.update_controls()
        self.Centre()
        # Open maximized by default so the results boxes get their full
        # expandable height right away, rather than the cramped height a
        # restored 940x780 window leaves them - previously this meant
        # manually maximizing was needed before NVDA had a comfortable
        # amount of visible text to work with.
        self.Maximize(True)
        # Warm the optional in-process Florence engine away from the UI thread.
        # This keeps the first Photo import responsive when a model is already
        # installed, while remaining a no-op when local AI is not configured.
        threading.Thread(
            target=self._preload_local_ai,
            name="ScanBox AI preload",
            daemon=True,
        ).start()
        if self.app_settings.get("check_for_updates_on_startup", True):
            # Wait until the frame is visible before a newer-release prompt
            # can appear. A startup check stays silent when current or offline.
            wx.CallLater(1500, self._check_for_updates_on_startup)

    def _preload_local_ai(self):
        try:
            announce("Checking for models.")
            if florence_ready():
                logger.info("Local AI model preloaded in background.")
            announce("Ready.")
        except Exception:
            logger.exception("Background local AI preload failed")

    def open_user_manual(self, event=None):
        """Open the bundled user manual in the default web browser."""
        if not os.path.isfile(MANUAL_PATH):
            wx.MessageBox(
                f"The ScanBox user manual could not be found:\n{MANUAL_PATH}",
                "User Manual",
                wx.OK | wx.ICON_ERROR,
                self,
            )
            return
        try:
            manual_url = "file:///" + MANUAL_PATH.replace("\\", "/")
            if not wx.LaunchDefaultBrowser(manual_url):
                raise OSError("Windows did not open the default browser.")
        except Exception as exc:
            logger.exception("Could not open the user manual")
            wx.MessageBox(
                f"ScanBox could not open the user manual.\n\n{exc}",
                "User Manual",
                wx.OK | wx.ICON_ERROR,
                self,
            )

    def show_about(self, event=None):
        """Show application identity and version information."""
        wx.MessageBox(
            f"{APP_NAME}\nVersion {APP_VERSION}\n\n"
            "A privacy-first scanning, recognition, and photo-description "
            "program for Windows.\n\n"
            "Scanning, recognition, and image descriptions are processed "
            "on this computer.",
            f"About {APP_NAME}",
            wx.OK | wx.ICON_INFORMATION,
            self,
        )

    @staticmethod
    def _version_parts(version):
        """Return a tuple suitable for comparing calendar-style versions."""
        match = re.fullmatch(r"\s*v?(\d+(?:\.\d+)*)\s*", str(version))
        if not match:
            raise ValueError(f"Invalid version number: {version}")
        return tuple(int(part) for part in match.group(1).split("."))

    def check_for_updates(self, event=None):
        """Fetch release information without blocking the application window."""
        self._start_update_check(show_current=True)

    def _check_for_updates_on_startup(self):
        self._start_update_check(show_current=False)

    def _start_update_check(self, show_current):
        manifest_url = (
            self.app_settings.get("update_manifest_url", "").strip()
            or UPDATE_MANIFEST_URL
        )
        if not manifest_url:
            if show_current:
                wx.MessageBox(
                    f"{APP_NAME} {APP_VERSION} is installed.\n\n"
                    "Automatic update information is not available for this build.",
                    "Check for Updates",
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return

        self.check_updates_item.Enable(False)
        if show_current:
            self.SetStatusText("Checking for ScanBox updates...")
        threading.Thread(
            target=self._check_for_updates_worker,
            args=(manifest_url, show_current),
            name="ScanBox update check",
            daemon=True,
        ).start()

    def _check_for_updates_worker(self, manifest_url, show_current):
        try:
            request = urllib.request.Request(
                manifest_url,
                headers={"User-Agent": f"{APP_NAME}/{APP_VERSION}"},
            )
            with urllib.request.urlopen(request, timeout=15) as response:
                manifest = json.loads(response.read().decode("utf-8"))
            # Accept both ScanBox's small update-manifest format and GitHub's
            # latest-release response.
            latest_version = str(
                manifest.get("version") or manifest["tag_name"]
            ).strip()
            self._version_parts(latest_version)
            download_url = str(
                manifest.get("url") or manifest.get("html_url", "")
            ).strip()
            notes = str(
                manifest.get("notes") or manifest.get("body", "")
            ).strip()
            result = (latest_version, download_url, notes, None)
        except Exception as exc:
            logger.exception("Update check failed")
            result = (None, "", "", str(exc))
        wx.CallAfter(self._finish_update_check, *result, show_current)

    def _finish_update_check(
        self, latest_version, download_url, notes, error, show_current
    ):
        self.check_updates_item.Enable(True)
        if show_current:
            self.SetStatusText("Ready")
        if error:
            if show_current:
                wx.MessageBox(
                    "ScanBox could not check for updates.\n\n"
                    "Check your internet connection and try again.\n\n"
                    f"Details: {error}",
                    "Check for Updates",
                    wx.OK | wx.ICON_ERROR,
                    self,
                )
            return

        if self._version_parts(latest_version) <= self._version_parts(APP_VERSION):
            if show_current:
                wx.MessageBox(
                    f"{APP_NAME} {APP_VERSION} is up to date.",
                    "Check for Updates",
                    wx.OK | wx.ICON_INFORMATION,
                    self,
                )
            return

        message = (
            f"ScanBox {latest_version} is available.\n"
            f"You currently have version {APP_VERSION}."
        )
        if notes:
            message += f"\n\n{notes}"
        style = wx.YES_NO | wx.ICON_INFORMATION if download_url else wx.OK | wx.ICON_INFORMATION
        if download_url:
            message += "\n\nWould you like to open the download page?"
        answer = wx.MessageBox(message, "ScanBox Update Available", style, self)
        if download_url and answer == wx.YES:
            wx.LaunchDefaultBrowser(download_url)

    def on_mode_change(self, event):
        if self.mode_tabs.GetSelection() == 2:
            self.refresh_photo_library()
        self.update_controls()
        event.Skip()

    def on_activate(self, event):
        # Windows already restores keyboard focus to whatever control last
        # had it when a top-level window is reactivated. Additional focus
        # handling can move screen-reader focus to the frame instead of the
        # previously active control, so leave the native behaviour intact.
        event.Skip()

    def _ensure_tray_icon(self):
        if self.tray_icon is None:
            self.tray_icon = ScanBoxTaskBarIcon(self)

    def on_iconize(self, event):
        if (
            event.IsIconized()
            and self.app_settings.get("minimize_to_notification_area", True)
        ):
            focus = wx.Window.FindFocus()
            if focus is not None:
                self._restore_focus = focus
            self._ensure_tray_icon()
            if self._toggle_minimize_announced:
                self._toggle_minimize_announced = False
                wx.CallAfter(self.Hide)
            else:
                wx.CallAfter(self._hide_in_notification_area)
        event.Skip()

    def _hide_in_notification_area(self):
        if self.IsIconized():
            announce("minimised.")
            if self._tray_hide_timer is not None:
                self._tray_hide_timer.Stop()
            self._tray_hide_timer = wx.CallLater(1000, self.Hide)

    def restore_from_notification_area(self, after_restore=None):
        if self._toggle_minimize_timer is not None:
            self._toggle_minimize_timer.Stop()
            self._toggle_minimize_timer = None
            self._toggle_minimize_announced = False
        if self._tray_hide_timer is not None:
            self._tray_hide_timer.Stop()
            self._tray_hide_timer = None
        if self._restore_timer is not None:
            self._restore_timer.Stop()
        announce("restored.")
        self._restore_timer = wx.CallLater(
            500, self._complete_restore, after_restore
        )

    def _complete_restore(self, after_restore=None):
        self._restore_timer = None
        self.Show(True)
        if self.IsIconized():
            self.Iconize(False)
        self.Raise()
        try:
            ctypes.windll.user32.SetForegroundWindow(self.GetHandle())
        except Exception:
            pass
        focus = self._restore_focus
        if focus is not None:
            try:
                focus.SetFocus()
            except Exception:
                self.mode_tabs.SetFocus()
        else:
            self.mode_tabs.SetFocus()
        if after_restore is not None:
            wx.CallAfter(after_restore)

    def toggle_window_visibility(self, event=None):
        try:
            is_foreground = (
                ctypes.windll.user32.GetForegroundWindow() == self.GetHandle()
            )
        except Exception:
            is_foreground = False
        if self.IsShown() and not self.IsIconized() and is_foreground:
            announce("minimised.")
            if self._toggle_minimize_timer is not None:
                self._toggle_minimize_timer.Stop()
            self._toggle_minimize_timer = wx.CallLater(
                500, self._complete_toggle_minimize
            )
        else:
            self.restore_from_notification_area()

    def _complete_toggle_minimize(self):
        self._toggle_minimize_timer = None
        # The iconize handler consumes this flag only when notification-area
        # behavior is enabled. Keep it clear during an ordinary minimize so
        # it cannot suppress a later notification-area announcement.
        self._toggle_minimize_announced = self.app_settings.get(
            "minimize_to_notification_area", True
        )
        self.Iconize(True)

    def on_screen_hotkey(self, event=None):
        if self.busy:
            return
        if self.photo_mode_blocked(True):
            return
        announce("please wait.")
        try:
            image = grab_foreground_window()
            path = os.path.join(TEMP_DIR, f"screen_{uuid.uuid4().hex}.png")
            image.save(path, "PNG")
        except Exception as exc:
            logger.exception("Could not capture the screen")
            wx.MessageBox(f"Could not capture the screen: {exc}", "Screen capture failed")
            return
        self.busy = True
        self.update_controls()
        threading.Thread(
            target=self._screen_description_worker,
            args=(path,),
            name="ScanBox screen description",
            daemon=True,
        ).start()

    def on_screen_ocr_hotkey(self, event=None):
        if self.busy:
            return
        announce("please wait.")
        try:
            image = grab_foreground_window()
            path = os.path.join(TEMP_DIR, f"screen_ocr_{uuid.uuid4().hex}.png")
            image.save(path, "PNG")
        except Exception as exc:
            logger.exception("Could not capture the screen for OCR")
            wx.MessageBox(f"Could not capture the screen: {exc}", "Screen capture failed")
            return
        self.busy = True
        self.update_controls()
        threading.Thread(
            target=self._screen_ocr_worker,
            args=(path,),
            name="ScanBox screen OCR",
            daemon=True,
        ).start()

    def _screen_ocr_worker(self, path):
        try:
            detect_and_crop_screen_document(path)
            text = self.process_document(
                path,
                allow_install_prompt=False,
                detect_page=False,
            )
        except Exception as exc:
            logger.exception("Screen OCR failed")
            text = f"Processing failed: {exc}"
        wx.CallAfter(self._finish_screen_description, path, text)

    def _screen_description_worker(self, path):
        try:
            text = self.process_photo(path, description_prompt=SCREEN_DESCRIBE_PROMPT)
        except Exception as exc:
            logger.exception("Screen description failed")
            text = f"Processing failed: {exc}"
        wx.CallAfter(self._finish_screen_description, path, text)

    def _finish_screen_description(self, path, text):
        self.busy = False
        self.append_output(text + "\n\n")
        self.update_controls()
        wx.CallLater(1000, announce, text)
        try:
            os.remove(path)
        except OSError:
            pass

    def on_photo_ocr_toggle(self, event):
        self.app_settings["photo_ocr_enabled"] = self.photo_ocr_checkbox.GetValue()
        write_app_settings(self.app_settings)

    def update_controls(self):
        has_session = bool(self.session_pages)
        is_photo_mode = self.mode_tabs.GetSelection() == 1
        is_library_mode = self.mode_tabs.GetSelection() == 2
        active_mode = "photo" if is_photo_mode else "document"
        has_scanned_images = any(
            source == "scan" and mode == active_mode
            for source, mode in zip(
                self.session_file_sources, self.session_file_modes
            )
        )
        has_document_text = any(
            mode == "document" for mode in self.session_page_modes
        )
        can_save_text = bool(
            has_document_text
            and not is_photo_mode
            and self.app_settings.get("append_text_to_buffer", False)
        )

        self.save_text_btn.SetLabel("Save Text")
        self.save_img_btn.SetLabel(
            "Save Scanned Photo" if is_photo_mode else "Save Scanned Images"
        )

        self.output_box.Show(not is_library_mode)
        self.mode_tabs_sizer_item.SetProportion(1 if is_library_mode else 0)
        self.save_text_btn.Show(can_save_text and not is_library_mode)
        self.save_img_btn.Show(has_scanned_images and not is_library_mode)
        self.discard_btn.Show(has_session and not is_library_mode)
        self.cancel_batch_btn.Show(
            getattr(self, "batch_cancel_event", None) is not None and self.busy
        )

        self.mode_tabs.Enable(not self.busy)
        self.document_scan_btn.Enable(not self.busy)
        self.document_import_btn.Enable(not self.busy)
        self.photo_scan_btn.Enable(not self.busy)
        self.photo_import_btn.Enable(not self.busy)
        self.photo_batch_import_btn.Enable(not self.busy)
        self.photo_ocr_checkbox.Enable(not self.busy)
        self.library_list.Enable(not self.busy)
        self.settings_btn.Enable(not self.busy)
        self.save_text_btn.Enable(
            can_save_text and not self.busy
        )
        self.save_img_btn.Enable(has_scanned_images and not self.busy)
        self.discard_btn.Enable(has_session and not self.busy)
        self.cancel_batch_btn.Enable(self.busy)

        if self.busy and self.busy_status_visible:
            self.SetStatusText("Please wait. Processing...")
        elif is_library_mode:
            self.SetStatusText(f"Stored photo descriptions: {len(self.photo_library)}")
        elif has_session:
            noun = "Photos processed" if is_photo_mode else "Pages scanned"
            self.SetStatusText(f"{noun}: {self.page_counter}")
        else:
            self.SetStatusText("No active session")

        self.Layout()

    def selected_library_index(self):
        return self.library_list.GetFirstSelected()

    def selected_library_entry(self):
        index = self.selected_library_index()
        if 0 <= index < len(self.photo_library):
            return self.photo_library[index]
        return None

    def checked_library_indices(self):
        return [
            index
            for index in range(self.library_list.GetItemCount())
            if self.library_list.IsItemChecked(index)
        ]

    def select_all_library_entries(self, event=None):
        for index in range(self.library_list.GetItemCount()):
            self.library_list.CheckItem(index, True)
        announce("All photo descriptions selected.")

    def refresh_photo_library(self, selected_id=None):
        self.photo_library = load_photo_library()
        self.library_list.DeleteAllItems()
        selected_index = -1
        for index, entry in enumerate(self.photo_library):
            path = resolve_stored_photo_path(entry)
            description = entry.get("description", "").replace("\r", " ").replace("\n", " ")
            row = self.library_list.InsertItem(index, description[:160])
            self.library_list.SetItem(
                row, 1, "Available" if os.path.isfile(path) else "Missing"
            )
            self.library_list.SetItem(
                row, 2, os.path.basename(path) or "(No filename)"
            )
            if selected_id and entry.get("id") == selected_id:
                selected_index = index
        if selected_index < 0 and self.photo_library:
            selected_index = 0
        if selected_index >= 0:
            self.library_list.Select(selected_index)
            self.library_list.Focus(selected_index)
        else:
            self.library_description.SetValue("")
        if hasattr(self, "library_open_btn"):
            has_entry = bool(self.photo_library)
            self.library_open_btn.Enable(has_entry)
            self.library_folder_btn.Enable(has_entry)
            self.library_copy_btn.Enable(has_entry)
            self.library_remove_btn.Enable(has_entry)
            self.library_select_all_btn.Enable(has_entry and not self.busy)

    def on_library_selected(self, event):
        entry = self.selected_library_entry()
        if entry:
            path = resolve_stored_photo_path(entry)
            self.library_description.SetValue(
                f"{entry.get('description', '')}\n\nPath: {path}"
            )
        event.Skip()

    def on_library_key(self, event):
        key = event.GetKeyCode()
        if key == wx.WXK_DELETE:
            self.remove_library_entry()
            return
        if key == wx.WXK_SPACE:
            index = self.library_list.GetFocusedItem()
            if index >= 0:
                self.library_list.CheckItem(
                    index, not self.library_list.IsItemChecked(index)
                )
            return
        if key in (wx.WXK_RETURN, wx.WXK_NUMPAD_ENTER):
            self.open_library_photo()
            return
        if key in (wx.WXK_LEFT, wx.WXK_RIGHT) and self.photo_library:
            current = max(0, self.selected_library_index())
            change = -1 if key == wx.WXK_LEFT else 1
            target = max(0, min(len(self.photo_library) - 1, current + change))
            self.library_list.Select(current, False)
            self.library_list.Select(target)
            self.library_list.Focus(target)
            return
        event.Skip()

    def on_library_context_menu(self, event):
        if not self.selected_library_entry():
            return
        menu = wx.Menu()
        open_item = menu.Append(wx.ID_OPEN, "Open Photo")
        folder_item = menu.Append(wx.ID_ANY, "Open Containing Folder")
        copy_item = menu.Append(wx.ID_COPY, "Copy Path")
        menu.AppendSeparator()
        remove_item = menu.Append(wx.ID_DELETE, "Remove Stored Description")
        menu.Bind(wx.EVT_MENU, self.open_library_photo, open_item)
        menu.Bind(wx.EVT_MENU, self.open_library_folder, folder_item)
        menu.Bind(wx.EVT_MENU, self.copy_library_path, copy_item)
        menu.Bind(wx.EVT_MENU, self.remove_library_entry, remove_item)
        self.PopupMenu(menu)
        menu.Destroy()

    def open_library_photo(self, event=None):
        entry = self.selected_library_entry()
        path = resolve_stored_photo_path(entry or {})
        if not path or not os.path.isfile(path):
            wx.MessageBox("The stored photo could not be found.", "Photo unavailable")
            return
        os.startfile(path)

    def open_library_folder(self, event=None):
        entry = self.selected_library_entry()
        path = resolve_stored_photo_path(entry or {})
        if not path or not os.path.exists(os.path.dirname(path)):
            wx.MessageBox("The containing folder could not be found.", "Folder unavailable")
            return
        subprocess.Popen(["explorer.exe", "/select,", path])

    def copy_library_path(self, event=None):
        entry = self.selected_library_entry()
        path = resolve_stored_photo_path(entry or {})
        if not path:
            return
        if wx.TheClipboard.Open():
            try:
                wx.TheClipboard.SetData(wx.TextDataObject(path))
                wx.TheClipboard.Flush()
            finally:
                wx.TheClipboard.Close()
        announce("Photo path copied.")

    def remove_library_entry(self, event=None):
        checked_indices = self.checked_library_indices()
        if checked_indices:
            entries = [self.photo_library[index] for index in checked_indices]
        else:
            entry = self.selected_library_entry()
            entries = [entry] if entry else []
        if not entries:
            return
        count = len(entries)
        description = (
            "these stored descriptions" if count != 1 else "this stored description"
        )
        answer = wx.MessageBox(
            f"Remove {description}? The original photo will not be deleted.",
            "Remove stored description",
            wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION,
        )
        if answer != wx.YES:
            return
        entry_ids = {entry.get("id") for entry in entries}
        remaining = [
            item for item in self.photo_library if item.get("id") not in entry_ids
        ]
        save_photo_library(remaining)
        self.refresh_photo_library()
        self.update_controls()
        announce(
            "Stored description%s removed. The photo%s not deleted."
            % ("s" if count != 1 else "", "s were" if count != 1 else " was")
        )

    def begin_busy(self, message, restore_focus=True):
        self.busy = True
        self.restore_focus_after_busy = restore_focus
        self.busy_status_visible = False
        self.busy_status_token += 1
        token = self.busy_status_token
        if restore_focus and not self.IsActive():
            # Bring ScanBox back to the front if focus drifted elsewhere
            # while a scan was underway (e.g. an external scanner dialog
            # took it). Skip this when ScanBox is already the active
            # window - as it always is right after Import, since the file
            # picker is owned by this same window and never hands focus
            # away. Re-asserting focus/activation when nothing actually
            # moved it fires a redundant focus-change announcement, which
            # was colliding with the screen reader announcing the result a
            # moment later on Import (reported as a clipped "ScanBox!" cut
            # off right before the description).
            self.Raise()
            self.RequestUserAttention()
        self.update_controls()
        wx.CallLater(1000, self.show_busy_status, token, message)
        wx.SafeYield(self, onlyIfNeeded=True)

    def show_busy_status(self, token, message):
        if not self.busy or token != self.busy_status_token:
            return
        self.busy_status_visible = True
        self.SetStatusText("Please wait. Processing...")
        announce(message)

    def end_busy(self):
        self.busy = False
        self.busy_status_visible = False
        self.busy_status_token += 1
        if getattr(self, "restore_focus_after_busy", True):
            self.Raise()
        self.update_controls()

    def scan_page(self):
        if win32com is None:
            wx.MessageBox(
                "Windows scanner support is not available. Use Import Image or "
                "install the Windows scanner support package.",
                "Scanner unavailable",
            )
            return None

        cd = win32com.client.Dispatch("WIA.CommonDialog")
        dev = cd.ShowSelectDevice()
        if not dev:
            return None

        item = dev.Items[1]
        tiff_format = "{B96B3CB1-0728-11D3-9D7B-0000F81EF32E}"
        img = cd.ShowTransfer(item, tiff_format)

        path = os.path.join(TEMP_DIR, f"scan_{uuid.uuid4().hex}.tif")
        with open(path, "wb") as f:
            f.write(img.FileData.BinaryData)
        return path

    def photo_mode_blocked(self, is_photo_mode):
        """In photo mode the model is required, so check before capturing."""
        if not is_photo_mode or vision_ready():
            return False
        self.offer_install_vision(
            "Photo descriptions require the optional local AI pack. The same "
            "pack also improves difficult document reading."
        )
        return True

    def on_scan(self, event, is_photo_mode):
        if self.photo_mode_blocked(is_photo_mode):
            return
        path = self.scan_page()
        if path:
            self.process_image(path, is_photo_mode, "scan")

    def on_import_image(self, event, is_photo_mode):
        # Let the activating mouse/key event finish before Windows creates its
        # native picker. Otherwise the picker can inherit that input and put
        # Explorer's Search box into its active search state.
        if event is not None:
            wx.CallAfter(self.on_import_image, None, is_photo_mode)
            return
        if self.photo_mode_blocked(is_photo_mode):
            return
        wildcard = (
            "Images and PDF (*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.pdf)|"
            "*.jpg;*.jpeg;*.png;*.tif;*.tiff;*.pdf"
        )
        last_dir = self.app_settings.get("last_import_dir", "")
        start_dir = last_dir if last_dir and os.path.isdir(last_dir) else IMAGES_DIR
        dlg = wx.FileDialog(
            self,
            "Import image or PDF",
            defaultDir=start_dir,
            wildcard=wildcard,
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST,
        )
        try:
            if dlg.ShowModal() == wx.ID_OK:
                source = dlg.GetPath()
                self.app_settings["last_import_dir"] = os.path.dirname(source)
                write_app_settings(self.app_settings)
                try:
                    if os.path.splitext(source)[1].lower() == ".pdf":
                        if is_photo_mode:
                            raise ValueError(
                                "PDF import is available on the Document tab."
                            )
                        self.process_pdf(source)
                    else:
                        path = self.copy_imported_image(source)
                        self.process_image(
                            path,
                            is_photo_mode,
                            "import",
                            library_path=source,
                        )
                except Exception as exc:
                    wx.MessageBox(
                        f"ScanBox could not import this file: {exc}",
                        "Import failed",
                    )
        finally:
            dlg.Destroy()

    def batch_import_photos(self, event=None):
        if self.photo_mode_blocked(True):
            return
        picker = wx.FileDialog(
            self,
            "Choose photos for batch import",
            defaultDir=(
                self.app_settings.get("last_import_dir")
                if self.app_settings.get("last_import_dir")
                and os.path.isdir(self.app_settings.get("last_import_dir"))
                else IMAGES_DIR
            ),
            wildcard="Images (*.jpg;*.jpeg;*.png;*.tif;*.tiff)|*.jpg;*.jpeg;*.png;*.tif;*.tiff",
            style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
        )
        try:
            if picker.ShowModal() != wx.ID_OK:
                return
            paths = picker.GetPaths()
            if paths:
                self.app_settings["last_import_dir"] = os.path.dirname(paths[0])
                write_app_settings(self.app_settings)
        finally:
            picker.Destroy()
        if not paths:
            return

        dialog = wx.Dialog(self, title="Select photos to import")
        checklist = wx.ListCtrl(
            dialog, style=wx.LC_REPORT | wx.LC_SINGLE_SEL
        )
        checklist.EnableCheckBoxes(True)
        checklist.InsertColumn(0, "Photo", width=500)
        for path in paths:
            index = checklist.InsertItem(checklist.GetItemCount(), os.path.basename(path))
            checklist.CheckItem(index, True)
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(checklist, 1, wx.ALL | wx.EXPAND, 10)

        def on_batch_check(event):
            index = event.GetIndex()
            if index >= 0:
                state = "Checked" if checklist.IsItemChecked(index) else "Unchecked"
                announce(f"{state} {checklist.GetItemText(index)}")
            event.Skip()

        checklist.Bind(wx.EVT_LIST_ITEM_CHECKED, on_batch_check)
        add_more = wx.Button(dialog, label="Add more photos...")

        def add_more_photos(event):
            more_picker = wx.FileDialog(
                dialog,
                "Add photos",
                defaultDir=(
                    self.app_settings.get("last_import_dir")
                    if self.app_settings.get("last_import_dir")
                    and os.path.isdir(self.app_settings.get("last_import_dir"))
                    else IMAGES_DIR
                ),
                wildcard="Images (*.jpg;*.jpeg;*.png;*.tif;*.tiff)|*.jpg;*.jpeg;*.png;*.tif;*.tiff",
                style=wx.FD_OPEN | wx.FD_FILE_MUST_EXIST | wx.FD_MULTIPLE,
            )
            try:
                if more_picker.ShowModal() != wx.ID_OK:
                    return
                more_paths = more_picker.GetPaths()
                if more_paths:
                    self.app_settings["last_import_dir"] = os.path.dirname(more_paths[0])
                    write_app_settings(self.app_settings)
                for path in more_paths:
                    if path not in paths:
                        paths.append(path)
                        index = checklist.InsertItem(
                            checklist.GetItemCount(), os.path.basename(path)
                        )
                        checklist.CheckItem(index, True)
            finally:
                more_picker.Destroy()

        add_more.Bind(wx.EVT_BUTTON, add_more_photos)
        sizer.Add(add_more, 0, wx.ALL | wx.ALIGN_LEFT, 10)
        buttons = dialog.CreateButtonSizer(wx.OK | wx.CANCEL)
        sizer.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        dialog.SetSizerAndFit(sizer)
        dialog.SetSize((600, 450))
        try:
            if dialog.ShowModal() != wx.ID_OK:
                return
            selected = [
                path
                for index, path in enumerate(paths)
                if checklist.IsItemChecked(index)
            ]
        finally:
            dialog.Destroy()
        if not selected:
            return
        self.prepare_output_buffer()
        self.busy = True
        self.busy_status_visible = False
        self.batch_cancel_event = threading.Event()
        wx.CallLater(1000, announce, "Working.")
        self.update_controls()
        threading.Thread(
            target=self._batch_import_worker,
            args=(selected,),
            name="ScanBox batch import",
            daemon=True,
        ).start()

    def _batch_import_worker(self, paths):
        completed = 0
        for source in paths:
            if self.batch_cancel_event.is_set():
                break
            try:
                path = self.copy_imported_image(source)
                text = self.process_photo(path, self.app_settings.get("photo_ocr_enabled", False))
                if not text.startswith("Processing failed:"):
                    add_photo_description(source, text)
                wx.CallAfter(self._finish_batch_item, source, text, completed + 1, len(paths))
            except Exception as exc:
                logger.exception("Batch photo import failed for %s", source)
                wx.CallAfter(self._finish_batch_item, source, f"Processing failed: {exc}", completed + 1, len(paths))
            completed += 1
        wx.CallAfter(self._finish_batch_done)

    def cancel_batch_import(self, event=None):
        if getattr(self, "batch_cancel_event", None) is not None:
            self.batch_cancel_event.set()
            self.SetStatusText("Cancelling batch photo import...")
            announce("Cancelling batch photo import.")

    def _finish_batch_item(self, source, text, number, total):
        self.output_box.Show(True)
        self.output_box.Enable()
        self.append_output(f"{os.path.basename(source)}\n{text}\n\n")
        self.output_box.SetFocusFromKbd()
        self.Layout()
        self.SetStatusText(f"Batch photo {number} of {total}")

    def _finish_batch_done(self):
        was_cancelled = (
            self.batch_cancel_event is not None
            and self.batch_cancel_event.is_set()
        )
        self.busy = False
        self.batch_cancel_event = None
        self.photo_batch_import_btn.Enable(True)
        self.refresh_photo_library()
        self.update_controls()
        if self.session_pages and self.mode_tabs.GetSelection() != 2:
            self.output_box.SetFocusFromKbd()
        announce(
            "Batch photo import cancelled."
            if was_cancelled
            else "Batch photo import complete."
        )
        if not was_cancelled:
            wx.MessageBox(
                "All selected photos have been processed and stored in the Photo Library.",
                "Batch photo import complete",
            )

    def copy_imported_image(self, source):
        ext = os.path.splitext(source)[1].lower() or ".jpg"
        dest = os.path.join(TEMP_DIR, f"import_{uuid.uuid4().hex}{ext}")
        try:
            with Image.open(source) as img:
                img = ImageOps.exif_transpose(img)
                img.save(dest)
            logger.info("Imported image source=%s working_copy=%s", source, dest)
            return dest
        except UnidentifiedImageError as exc:
            raise ValueError("that file is not a supported image or PDF.") from exc

    def process_pdf(self, source):
        open_word = bool(
            self.app_settings.get("open_word_after_pdf_conversion", False)
            and microsoft_word_available()
        )
        show_text = self.app_settings.get("render_converted_in_window", True)
        if not open_word and not show_text:
            raise ValueError(
                "Choose a PDF reading destination in Settings before importing "
                "a PDF."
            )
        if open_word and not os.path.exists(PDF2WORD_EXE):
            raise ValueError("PDF to Word support is not installed in ScanBox.")
        if fitz is None:
            raise ValueError("PDF page reading support is not installed in ScanBox.")

        self.prepare_output_buffer()
        self.begin_busy("Please wait.", restore_focus=False)
        threading.Thread(
            target=self._process_pdf_worker,
            args=(source, open_word),
            daemon=True,
        ).start()

    def _process_pdf_worker(self, source, open_word):
        try:
            if not open_word:
                pages = self.read_pdf_pages(source)
                wx.CallAfter(self._finish_pdf_text, pages)
                return

            if self.pdf_suitable_for_pdf2word(source):
                output_path = self.convert_pdf_with_pdf2word(source)
            else:
                pages = self.read_pdf_pages(source)
                output_path = self.write_pdf_pages_to_docx(source, pages)
            wx.CallAfter(self._finish_pdf_conversion, output_path)
        except Exception as exc:
            wx.CallAfter(self._finish_pdf_error, f"Processing failed: {exc}")

    def render_pdf_page(self, page, page_number):
        scale = 2.0
        pixmap = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=False)
        path = os.path.join(
            TEMP_DIR, f"pdf_{page_number + 1}_{uuid.uuid4().hex}.png"
        )
        pixmap.save(path)
        return path

    def pdf_suitable_for_pdf2word(self, source):
        """Sample the first, middle and final page before choosing PDF2Word."""
        with fitz.open(source) as pdf:
            if pdf.page_count == 0:
                raise ValueError("the PDF contains no pages.")
            indices = sorted({0, pdf.page_count // 2, pdf.page_count - 1})
            for index in indices:
                page = pdf.load_page(index)
                selectable = page.get_text("text").strip()
                if selectable:
                    logger.info(
                        "PDF sample page %s has selectable text; using PDF2Word candidate.",
                        index + 1,
                    )
                    continue
                image_path = self.render_pdf_page(page, index)
                try:
                    _text, confidence = ocr_local_with_confidence(image_path)
                finally:
                    try:
                        os.remove(image_path)
                    except OSError:
                        pass
                logger.info(
                    "PDF sample page %s OCR confidence %.1f",
                    index + 1,
                    confidence,
                )
                if confidence < CONF_THRESHOLD:
                    return False
        return True

    def read_pdf_pages(self, source):
        """Return local text for each PDF page, invoking AI only when needed."""
        pages = []
        with fitz.open(source) as pdf:
            if pdf.page_count == 0:
                raise ValueError("the PDF contains no pages.")
            for index in range(pdf.page_count):
                page = pdf.load_page(index)
                selectable = page.get_text("text").strip()
                if selectable:
                    text = selectable
                else:
                    image_path = self.render_pdf_page(page, index)
                    try:
                        text = self.process_document(
                            image_path, allow_install_prompt=False
                        )
                    finally:
                        try:
                            os.remove(image_path)
                        except OSError:
                            pass
                pages.append(text.strip())
        return pages

    def write_pdf_pages_to_docx(self, source, pages):
        output_path = self.pdf2word_output_path(source)
        document = docx.Document()
        for index, text in enumerate(pages):
            if index:
                document.add_page_break()
            for block in text.split("\n\n"):
                document.add_paragraph(block)
        document.save(output_path)
        return output_path

    def convert_pdf_with_pdf2word(self, source):
        output_path = self.pdf2word_output_path(source)
        env = os.environ.copy()
        if os.path.exists(PDF2WORD_TESSERACT):
            env["PATH"] = PDF2WORD_TESSERACT_DIR + os.pathsep + env.get("PATH", "")
            env["TESSDATA_PREFIX"] = PDF2WORD_TESSDATA
        result = subprocess.run(
            [PDF2WORD_EXE, source, output_path, "--ocr"],
            cwd=PDF2WORD_DIR,
            capture_output=True,
            text=True,
            timeout=600,
            env=env,
            **NO_WINDOW,
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "No error details returned."
            raise RuntimeError(f"PDF2Word failed: {error}")
        if not os.path.exists(output_path):
            raise RuntimeError("PDF2Word finished but did not create a DOCX file.")
        return output_path

    def pdf2word_output_path(self, source):
        base = os.path.splitext(os.path.basename(source))[0].strip() or "document"
        base = "".join(ch if ch not in '<>:"/\\|?*' else "_" for ch in base)
        output_path = os.path.join(OUTPUT_DIR, f"{base}.docx")
        if not os.path.exists(output_path):
            return output_path
        for index in range(2, 1000):
            candidate = os.path.join(OUTPUT_DIR, f"{base} {index}.docx")
            if not os.path.exists(candidate):
                return candidate
        return os.path.join(OUTPUT_DIR, f"{base} {uuid.uuid4().hex}.docx")

    def _finish_pdf_conversion(self, output_path):
        self.end_busy()
        self.page_counter += 1
        self.created_output_files.append(output_path)

        message = f"PDF converted to Word:\n{output_path}"
        self.session_pages.append(message)
        self.session_page_modes.append("document")
        self.append_output(message + "\n\n")
        announce("PDF converted to Word.")
        self.open_document_file(output_path)
        self.update_controls()

    def _finish_pdf_text(self, pages):
        self.end_busy()
        rendered_pages = []
        show_headings = self.app_settings.get("show_page_headings", False)
        for text in pages:
            self.page_counter += 1
            self.session_pages.append(text)
            self.session_page_modes.append("document")
            if show_headings:
                rendered_pages.append(
                    f"--- Document Page {self.page_counter} ---\n{text}"
                )
            else:
                rendered_pages.append(text)
        rendered = "\n\n".join(rendered_pages).strip()
        if rendered and self.app_settings.get("render_converted_in_window", True):
            self.append_output(rendered + "\n\n")
            announce("\n\n".join(pages))
        elif rendered:
            self.SetStatusText("PDF reading completed.")
        self.update_controls()

    def open_document_file(self, path):
        try:
            os.startfile(path)
        except Exception:
            pass

    def _finish_pdf_error(self, message):
        self.end_busy()
        wx.MessageBox(message, "Import failed")

    def process_image(
        self, path, is_photo_mode, source_kind, library_path=None
    ):
        self.prepare_output_buffer()
        self.page_counter += 1
        self.session_files.append(path)
        self.session_file_sources.append(source_kind)
        self.session_file_modes.append("photo" if is_photo_mode else "document")

        use_photo_ocr = bool(
            is_photo_mode and self.app_settings.get("photo_ocr_enabled", False)
        )
        self.begin_busy("Please wait.", restore_focus=source_kind != "import")
        threading.Thread(
            target=self._process_image_worker,
            args=(
                path,
                self.page_counter,
                is_photo_mode,
                use_photo_ocr,
                library_path or path,
            ),
            daemon=True,
        ).start()

    def _process_image_worker(
        self, path, page_number, is_photo_mode, use_photo_ocr, library_path
    ):
        try:
            if is_photo_mode:
                text = self.process_photo(path, use_photo_ocr)
            else:
                text = self.process_document(path, allow_install_prompt=False)
        except Exception as exc:
            logger.exception("Image processing failed for %s", path)
            text = f"Processing failed: {exc}"
        logger.info(
            "Processed image page=%s photo_mode=%s path=%s\nresult:\n%s",
            page_number,
            is_photo_mode,
            path,
            text,
        )
        wx.CallAfter(
            self._finish_image_processing,
            page_number,
            is_photo_mode,
            text,
            library_path,
        )

    def _finish_image_processing(self, page_number, is_photo_mode, text, source_path):
        if is_photo_mode:
            rendered = text + "\n\n"
        elif self.app_settings.get("show_page_headings", False):
            rendered = f"--- Document Page {page_number} ---\n{text}\n\n"
        else:
            rendered = text + "\n\n"
        self.end_busy()
        self.session_pages.append(text)
        self.session_page_modes.append("photo" if is_photo_mode else "document")
        if is_photo_mode and not text.startswith("Processing failed:"):
            try:
                add_photo_description(source_path, text)
                self.refresh_photo_library()
            except OSError:
                logger.exception("Could not store photo description for %s", source_path)
        self.append_output(rendered)
        self.output_box.SetFocusFromKbd()
        if not is_photo_mode:
            wx.CallLater(1000, announce, text)
        self.update_controls()

    def process_document(self, path, allow_install_prompt=True, detect_page=True):
        # Camera-based captures (a document camera, or a handheld/desktop
        # scanner like a Pearl/IRIScan-style device) photograph the page
        # against a desk/background rather than scanning it edge-to-edge.
        # Find and straighten the page before OCR. Safe no-op on flatbed/WIA
        # scans, which are already edge-to-edge.
        if detect_page:
            detect_and_crop_page(path)
        orientation = detect_orientation(path)
        text, confidence = ocr_local_with_confidence(path)

        if text.startswith("[Local OCR failed:"):
            return text

        if confidence >= CONF_THRESHOLD:
            return f"{orientation}\n\n{text}".strip()

        # Handwriting used to be filtered out here before any vision model
        # got a chance, on the assumption a vision model would handle it as
        # poorly as Tesseract does. That's not a safe assumption for every
        # pack - Florence-2, for instance, has real (if imperfect)
        # handwriting OCR training - so try the vision model first when one
        # is installed, and only fall back to a flat "can't read this"
        # message if none is available at all.
        if vision_ready("transcribe"):
            # A low-confidence OCR draft is more likely to mislead the model
            # than help it, so let the document model read the image directly.
            return run_vision_task("transcribe", path)

        if looks_like_unsupported_handwriting(text, confidence):
            return "This page appears to contain handwriting that could not be read reliably."

        if not allow_install_prompt:
            return (
                "This document could not be read reliably. Install the local AI "
                "pack for improved document reading."
            )
        if self.offer_install_vision(
            "This document could not be read reliably. The local AI pack can "
            "provide a better reading."
        ):
            return (
                "The local AI pack is being installed. Scan or import this page "
                "again after installation."
            )
        return "This document could not be read reliably."

    def process_photo(self, path, use_ocr=False, description_prompt=None):
        # Runs on a worker thread, so it must not show any wx dialog. The
        # main-thread photo_mode_blocked() check already prompts to install
        # before we get here; this is just a defensive fallback.
        if not vision_ready():
            return "Photo description needs the local AI model. It is not installed."

        jpg_path = self.ensure_jpg(path)

        # Auto-crop is deliberately NOT run here. General photos (people,
        # scenes) very often contain strong rectangular shapes in the
        # background - door frames, windows, buildings - and cropping to
        # the wrong rectangle silently discards the actual subject with no
        # way for a user who can't see the photo to notice. It's only safe
        # to assume "the frame is one page" on the Document tab.

        # No auto-rotation here. The local model's own "is this upright,
        # yes/no" self-judgment turned out to be unreliable - it rotated at
        # least one already-correct photo (a book cover) because it
        # confidently misjudged its own orientation. There's no dependable
        # way to detect this with a model this size, so it isn't attempted.

        ocr_draft = ""
        if use_ocr:
            candidate, confidence = ocr_local_with_confidence(jpg_path)
            if (
                confidence >= PHOTO_OCR_CONF_THRESHOLD
                and not candidate.startswith("[Local OCR failed:")
            ):
                ocr_draft = candidate
            logger.info(
                "Photo OCR grounding confidence=%.1f used=%s",
                confidence,
                bool(ocr_draft),
            )
        return run_vision_task(
            "describe",
            jpg_path,
            ocr_draft,
            prompt_override=description_prompt,
        )

    def ensure_jpg(self, path):
        jpg_path = os.path.splitext(path)[0] + ".jpg"
        if os.path.abspath(path).lower() == os.path.abspath(jpg_path).lower():
            return path
        with Image.open(path) as img:
            ImageOps.exif_transpose(img).convert("RGB").save(jpg_path, "JPEG", quality=95)
        return jpg_path

    def append_output(self, text):
        self.output_box.Enable()
        start_pos = self.output_box.GetLastPosition()
        self.output_box.AppendText(text)
        self.output_box.SetInsertionPoint(start_pos)
        self.output_box.ShowPosition(start_pos)

    def prepare_output_buffer(self):
        """Start a fresh result unless the user enabled multi-page appending."""
        if self.app_settings.get("append_text_to_buffer", False):
            return
        self.session_files = []
        self.session_file_sources = []
        self.session_file_modes = []
        self.session_pages = []
        self.session_page_modes = []
        self.page_counter = 0
        self.output_box.Clear()
        self.output_box.Disable()

    def open_settings(self, event=None):
        dlg = wx.Dialog(self, title="Settings")
        root = wx.BoxSizer(wx.VERTICAL)

        notebook = wx.Notebook(dlg)

        general_panel = wx.Panel(notebook)
        general_panel.SetAccessible(_NamedPageAccessible(general_panel, "General"))
        general_sizer = wx.BoxSizer(wx.VERTICAL)
        delete_output = wx.CheckBox(general_panel, label="Delete output files upon exit")
        pdf_destination = wx.RadioBox(
            general_panel,
            label="PDF reading destination",
            choices=[
                "Show PDF reading in ScanBox",
                "Create a DOCX and open it in Microsoft Word",
            ],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        append_buffer = wx.CheckBox(
            general_panel,
            label="Append text to buffer for each Scan or Import",
        )
        show_page_headings = wx.CheckBox(
            general_panel,
            label="Show document page headings in results",
        )
        diagnostic_logging = wx.CheckBox(
            general_panel, label="Enable diagnostic logging"
        )
        check_updates_on_startup = wx.CheckBox(
            general_panel, label="Check for updates on startup"
        )
        minimize_to_notification_area = wx.CheckBox(
            general_panel,
            label="Keep ScanBox in the notification area when minimized",
        )
        diagnostic_logging.SetToolTip(
            f"Write scan, import, and local AI details to {LOG_FILE}"
        )
        delete_output.SetValue(self.app_settings.get("delete_output_files_on_exit", False))
        word_available = microsoft_word_available()
        pdf_destination.SetSelection(
            1
            if word_available
            and self.app_settings.get("open_word_after_pdf_conversion", False)
            else 0
        )
        pdf_destination.EnableItem(1, word_available)
        if not word_available:
            pdf_destination.SetToolTip(
                "Microsoft Word was not found; PDF readings will be shown in ScanBox."
            )
        append_buffer.SetValue(
            self.app_settings.get("append_text_to_buffer", False)
        )
        show_page_headings.SetValue(
            self.app_settings.get("show_page_headings", False)
        )
        diagnostic_logging.SetValue(
            self.app_settings.get("diagnostic_logging", False)
        )
        check_updates_on_startup.SetValue(
            self.app_settings.get("check_for_updates_on_startup", True)
        )
        minimize_to_notification_area.SetValue(
            self.app_settings.get("minimize_to_notification_area", True)
        )
        general_sizer.Add(delete_output, 0, wx.ALL, 10)
        general_sizer.Add(
            check_updates_on_startup, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10
        )
        general_sizer.Add(
            minimize_to_notification_area,
            0,
            wx.LEFT | wx.RIGHT | wx.BOTTOM,
            10,
        )
        general_sizer.Add(pdf_destination, 0, wx.ALL | wx.EXPAND, 10)
        general_sizer.Add(append_buffer, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        general_sizer.Add(
            show_page_headings, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10
        )
        general_sizer.Add(
            diagnostic_logging, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10
        )
        general_panel.SetSizer(general_sizer)
        notebook.AddPage(general_panel, "General")

        ai_panel = wx.Panel(notebook)
        ai_panel.SetAccessible(_NamedPageAccessible(ai_panel, "AI"))
        ai_sizer = wx.BoxSizer(wx.VERTICAL)

        # A plain file-presence check, not florence_ready() - that loads the
        # full ONNX Runtime engine into memory (a real cost for the ~1GB
        # large pack), which isn't warranted just to draw this tab.
        base_installed = _find_florence_files("base") is not None
        large_installed = _find_florence_files("large") is not None

        # List only the sizes actually installed, rather than showing both
        # and disabling one - a radio item that's both selected and disabled
        # breaks Tab navigation and confuses screen readers (this happened
        # before). With only installed sizes ever offered, every item shown
        # is always enabled, so that trap can't occur, and with exactly one
        # size installed this is a genuine, single-item, fully focusable
        # radio button rather than unreachable static text.
        model_choice = None
        model_choice_sizes = []
        if base_installed:
            model_choice_sizes.append(("base", "Smaller - quicker, less accurate"))
        if large_installed:
            model_choice_sizes.append(("large", "Larger - slower, improved accuracy"))

        if model_choice_sizes:
            model_choice = wx.RadioBox(
                ai_panel,
                label="Photo/document model",
                choices=[label for _, label in model_choice_sizes],
                majorDimension=1,
                style=wx.RA_SPECIFY_ROWS,
            )
            current_size = self.app_settings.get("florence_model_size", "base")
            sizes_shown = [size for size, _ in model_choice_sizes]
            selected_index = (
                sizes_shown.index(current_size) if current_size in sizes_shown else 0
            )
            model_choice.SetSelection(selected_index)
            ai_sizer.Add(model_choice, 0, wx.ALL | wx.EXPAND, 10)
            if len(model_choice_sizes) == 1:
                model_choice.SetToolTip(
                    "Install the other size below to be able to choose "
                    "between them here."
                )
        else:
            ai_sizer.Add(
                wx.StaticText(
                    ai_panel,
                    label=(
                        "No local AI model is installed yet. Use 'Install or "
                        "update local AI model' below to get started."
                    ),
                ),
                0,
                wx.ALL,
                10,
            )

        delete_model_btn = wx.Button(ai_panel, label="Delete selected model")
        delete_model_btn.Enable(model_choice is not None)

        def on_delete_model(event):
            if model_choice is None:
                return
            selected_size, _ = model_choice_sizes[model_choice.GetSelection()]
            size_name = "smaller" if selected_size == "base" else "larger"
            if wx.MessageBox(
                f"Delete the {size_name} local AI model from this computer? "
                "You can reinstall it later from 'Install or update local AI "
                "model'. Settings will close after this.",
                "Delete Model",
                wx.YES_NO | wx.ICON_WARNING,
            ) != wx.YES:
                return
            target_dir = os.path.join(VISION_DIR, FLORENCE_SIZE_SUBDIRS[selected_size])
            try:
                shutil.rmtree(target_dir)
            except OSError as exc:
                wx.MessageBox(
                    f"Could not delete the {size_name} model: {exc}", "Delete Model"
                )
                return
            _florence_cache.pop(selected_size, None)
            remaining = [s for s, _ in model_choice_sizes if s != selected_size]
            if (
                self.app_settings.get("florence_model_size", "base") == selected_size
                and remaining
            ):
                self.app_settings["florence_model_size"] = remaining[0]
                write_app_settings(self.app_settings)
            wx.MessageBox(
                f"The {size_name} model has been deleted. Settings will now "
                "close; reopen it to continue.",
                "Delete Model",
            )
            dlg.EndModal(wx.ID_CANCEL)

        delete_model_btn.Bind(wx.EVT_BUTTON, on_delete_model)
        ai_sizer.Add(delete_model_btn, 0, wx.ALL, 10)

        install_ai_btn = wx.Button(ai_panel, label="Install or update local AI model")
        custom_ai_btn = wx.Button(ai_panel, label="Set custom AI command")
        install_ai_btn.Bind(wx.EVT_BUTTON, self.install_local_ai)
        custom_ai_btn.Bind(wx.EVT_BUTTON, self.set_vision_command)
        ai_sizer.Add(install_ai_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        ai_sizer.Add(custom_ai_btn, 0, wx.LEFT | wx.RIGHT | wx.BOTTOM, 10)
        ai_panel.SetSizer(ai_sizer)
        notebook.AddPage(ai_panel, "AI")

        buttons = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        root.Add(notebook, 1, wx.ALL | wx.EXPAND, 10)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        dlg.SetSizer(root)
        dlg.SetSize((540, 440))
        dlg.Fit()

        try:
            if dlg.ShowModal() == wx.ID_OK:
                self.app_settings["delete_output_files_on_exit"] = delete_output.GetValue()
                self.app_settings["check_for_updates_on_startup"] = (
                    check_updates_on_startup.GetValue()
                )
                self.app_settings["minimize_to_notification_area"] = (
                    minimize_to_notification_area.GetValue()
                )
                open_in_word = (
                    word_available and pdf_destination.GetSelection() == 1
                )
                self.app_settings["open_word_after_pdf_conversion"] = open_in_word
                self.app_settings["render_converted_in_window"] = not open_in_word
                self.app_settings["append_text_to_buffer"] = append_buffer.GetValue()
                self.app_settings["show_page_headings"] = (
                    show_page_headings.GetValue()
                )
                logging_changed = (
                    self.app_settings.get("diagnostic_logging", False)
                    != diagnostic_logging.GetValue()
                )
                self.app_settings["diagnostic_logging"] = diagnostic_logging.GetValue()
                # model_choice only exists when at least one size is
                # installed, and only ever lists installed sizes, so
                # whichever item is selected is always safe to save.
                if model_choice is not None:
                    self.app_settings["florence_model_size"] = (
                        model_choice_sizes[model_choice.GetSelection()][0]
                    )
                write_app_settings(self.app_settings)
                if (
                    not self.app_settings["minimize_to_notification_area"]
                    and self.tray_icon is not None
                ):
                    self.tray_icon.RemoveIcon()
                    self.tray_icon.Destroy()
                    self.tray_icon = None
                if logging_changed:
                    configure_logging(diagnostic_logging.GetValue())
                    self.SetStatusText(
                        "Diagnostic logging "
                        + ("on" if diagnostic_logging.GetValue() else "off")
                    )
                self.update_controls()
        finally:
            dlg.Destroy()

    def on_close(self, event):
        if getattr(self, "_close_after_announcement", False):
            self._close_after_announcement = False
        else:
            if self.busy or self.installing:
                wx.MessageBox(
                    "ScanBox is still processing or installing local AI. Please "
                    "wait for it to finish before closing so temporary input files "
                    "are not removed while they are in use.",
                    "ScanBox is busy",
                )
                if event.CanVeto():
                    event.Veto()
                    return
            announce("Exiting.")
            if event.CanVeto():
                self._close_after_announcement = True
                event.Veto()
                wx.CallLater(500, self.Close)
                return
        clear_temp_directory()
        if self.app_settings.get("delete_output_files_on_exit"):
            self.delete_created_output_files()
        if self._toggle_minimize_timer is not None:
            self._toggle_minimize_timer.Stop()
            self._toggle_minimize_timer = None
        if self._tray_hide_timer is not None:
            self._tray_hide_timer.Stop()
            self._tray_hide_timer = None
        if self._restore_timer is not None:
            self._restore_timer.Stop()
            self._restore_timer = None
        try:
            self.UnregisterHotKey(self.screen_hotkey_id)
        except Exception:
            pass
        try:
            self.UnregisterHotKey(self.screen_ocr_hotkey_id)
        except Exception:
            pass
        try:
            self.UnregisterHotKey(self.toggle_window_hotkey_id)
        except Exception:
            pass
        if self.tray_icon is not None:
            self.tray_icon.RemoveIcon()
            self.tray_icon.Destroy()
            self.tray_icon = None
        event.Skip()

    def delete_created_output_files(self):
        output_root = os.path.abspath(OUTPUT_DIR)
        for file_path in list(self.created_output_files):
            try:
                target = os.path.abspath(file_path)
                if (
                    os.path.commonpath([output_root, target]) == output_root
                    and os.path.exists(target)
                ):
                    os.remove(target)
            except OSError:
                pass

    def save_text(self, event):
        if event is not None:
            wx.CallAfter(self.save_text, None)
            return
        merged = "\n\n".join(
            text
            for text, mode in zip(self.session_pages, self.session_page_modes)
            if mode == "document"
        )
        dlg = wx.FileDialog(
            self,
            "Save text",
            defaultDir=OUTPUT_DIR,
            wildcard="Text (*.txt)|*.txt|Word (*.docx)|*.docx|PDF (*.pdf)|*.pdf",
            style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
        )
        formats = ["txt", "docx", "pdf"]
        try:
            if dlg.ShowModal() == wx.ID_OK:
                path = dlg.GetPath()
                fmt = os.path.splitext(path)[1].lower().lstrip(".")
                if fmt not in formats:
                    # No (or unknown) extension typed: use the chosen file type.
                    fmt = formats[dlg.GetFilterIndex()]
                    path = path + "." + fmt
                save_text_output(merged, path, fmt)
        finally:
            dlg.Destroy()

    def save_images(self, event):
        if event is not None:
            wx.CallAfter(self.save_images, None)
            return
        active_mode = (
            "photo" if self.mode_tabs.GetSelection() == 1 else "document"
        )
        scanned_paths = [
            path
            for path, source, mode in zip(
                self.session_files,
                self.session_file_sources,
                self.session_file_modes,
            )
            if source == "scan" and mode == active_mode
        ]
        if not scanned_paths:
            return
        if len(scanned_paths) == 1:
            default_name = (
                "Scanned photo.jpg"
                if active_mode == "photo"
                else "Scanned document.jpg"
            )
            dlg = wx.FileDialog(
                self,
                "Save scanned image",
                defaultDir=IMAGES_DIR,
                defaultFile=default_name,
                wildcard="JPEG image (*.jpg)|*.jpg",
                style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT,
            )
            try:
                if dlg.ShowModal() == wx.ID_OK:
                    destination = self.write_scanned_jpeg(
                        scanned_paths[0], dlg.GetPath()
                    )
                    if active_mode == "photo":
                        update_photo_library_path(scanned_paths[0], destination)
                        self.refresh_photo_library()
            finally:
                dlg.Destroy()
            return

        name_dlg = wx.TextEntryDialog(self, "Base filename for scanned images:")
        try:
            if name_dlg.ShowModal() != wx.ID_OK:
                return
            base = self.safe_filename_stem(name_dlg.GetValue())
        finally:
            name_dlg.Destroy()
        folder_dlg = wx.DirDialog(
            self, "Choose a folder for scanned images", defaultPath=IMAGES_DIR
        )
        try:
            if folder_dlg.ShowModal() != wx.ID_OK:
                return
            folder = folder_dlg.GetPath()
        finally:
            folder_dlg.Destroy()
        for index, image_path in enumerate(scanned_paths, 1):
            target = self.available_image_path(
                folder, f"{base} {index}.jpg"
            )
            destination = self.write_scanned_jpeg(image_path, target)
            if active_mode == "photo":
                update_photo_library_path(image_path, destination)
        if active_mode == "photo":
            self.refresh_photo_library()

    def safe_filename_stem(self, value):
        cleaned = "".join(
            character if character not in '<>:"/\\|?*' else "_"
            for character in value.strip()
        ).rstrip(" .")
        return cleaned or "Scanned image"

    def available_image_path(self, folder, filename):
        target = os.path.join(folder, filename)
        if not os.path.exists(target):
            return target
        stem, extension = os.path.splitext(filename)
        for number in range(2, 1000):
            candidate = os.path.join(folder, f"{stem} ({number}){extension}")
            if not os.path.exists(candidate):
                return candidate
        return os.path.join(folder, f"{stem} {uuid.uuid4().hex}{extension}")

    def write_scanned_jpeg(self, source, destination):
        stem, extension = os.path.splitext(destination)
        if extension.lower() not in {".jpg", ".jpeg"}:
            destination = (stem if extension else destination) + ".jpg"
        with Image.open(source) as img:
            ImageOps.exif_transpose(img).convert("RGB").save(
                destination, "JPEG", quality=95
            )
        return destination

    def _pick_florence_model_size(self, intro_text):
        """Ask which Florence-2 size to install. Returns "base", "large", or
        None if the user cancelled."""
        dlg = wx.Dialog(self, title="Install Local AI")
        root = wx.BoxSizer(wx.VERTICAL)
        intro = wx.StaticText(dlg, label=intro_text)
        intro.Wrap(420)
        size_choice = wx.RadioBox(
            dlg,
            label="Model size",
            choices=[
                "Smaller - quicker, less accurate (about 357 MB)",
                "Larger - slower, improved accuracy (about 1.06 GB)",
            ],
            majorDimension=1,
            style=wx.RA_SPECIFY_ROWS,
        )
        default_size = self.app_settings.get("florence_model_size", "base")
        size_choice.SetSelection(1 if default_size == "large" else 0)
        root.Add(intro, 0, wx.ALL, 10)
        root.Add(size_choice, 0, wx.ALL | wx.EXPAND, 10)
        buttons = dlg.CreateButtonSizer(wx.OK | wx.CANCEL)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 10)
        dlg.SetSizer(root)
        dlg.Fit()
        try:
            if dlg.ShowModal() != wx.ID_OK:
                return None
            return "large" if size_choice.GetSelection() == 1 else "base"
        finally:
            dlg.Destroy()

    def install_local_ai(self, event):
        if self.installing:
            wx.MessageBox("The local AI model is already downloading.", "ScanBox")
            return
        size = self._pick_florence_model_size(
            "ScanBox will install its local AI pack for photo descriptions and "
            "improved reading of difficult documents. Photographs and "
            "documents always remain on this computer. Choose which model "
            "size to download - both can be installed, and you can switch "
            "between them later from Settings > AI."
        )
        if size is None:
            return
        self.start_install(size)

    def offer_install_vision(self, reason):
        """Ask whether to download the local AI model and start it if agreed.
        Returns True if a download was started (or is already running)."""
        if self.installing:
            return True
        size = self._pick_florence_model_size(
            reason
            + " Choose which model size to download - both can be installed, "
            "and you can switch between them later from Settings > AI. "
            "Processing always remains on this computer."
        )
        if size is None:
            return False
        self.start_install(size)
        return True

    def start_install(self, size="base"):
        if self.installing:
            return
        self.installing = True
        self._installing_size = size
        self.install_gauge.SetValue(0)
        self.install_gauge.Show()
        self.Layout()
        self.SetStatusText("Installing local AI pack. Downloading...")
        announce(
            "Installing local AI. Downloading. This can take several minutes."
        )
        threading.Thread(target=self._run_install, args=(size,), daemon=True).start()

    def _run_install(self, size):
        def status(message):
            wx.CallAfter(self._install_status, message)

        result = install_local_ai_pack(status, size)
        wx.CallAfter(self._install_done, result, size)

    def _install_status(self, message):
        progress = re.search(r":\s*(\d+)%\s+of\s+", message)
        if progress:
            percentage = int(progress.group(1))
            self.install_gauge.SetValue(percentage)
            # The native progress event is continuous; keep speech and the text
            # log concise enough to remain useful.
            if percentage not in (0, 100) and percentage % 25:
                return
        else:
            self.install_gauge.Pulse()
        self.SetStatusText(message)
        announce(message)

    def _install_done(self, result, size="base"):
        self.installing = False
        if result.startswith("Local AI pack installed"):
            self.install_gauge.SetValue(100)
            # Make the pack just installed the active one right away, rather
            # than leaving whichever size was previously selected in charge.
            self.app_settings["florence_model_size"] = size
            write_app_settings(self.app_settings)
        else:
            self.install_gauge.SetValue(0)
        self.install_gauge.Hide()
        self.Layout()
        self.SetStatusText(result)
        announce(result)
        wx.MessageBox(result, "Install Local AI")

    def set_vision_command(self, event):
        current = read_vision_command()
        message = (
            "Advanced setup. Enter a local AI command. You can use placeholders: "
            "{task}, {image}, {prompt}, and {draft}."
        )
        dlg = wx.TextEntryDialog(self, message, "Local AI Setup", current)
        try:
            if dlg.ShowModal() == wx.ID_OK:
                write_vision_command(dlg.GetValue())
                wx.MessageBox("Local AI command saved.", "ScanBox")
        finally:
            dlg.Destroy()

    def reset_session(self, event=None):
        for file_path in self.session_files:
            try:
                temp_root = os.path.abspath(TEMP_DIR)
                target = os.path.abspath(file_path)
                if os.path.commonpath([temp_root, target]) == temp_root and os.path.exists(target):
                    os.remove(target)
            except OSError:
                pass

        self.session_files = []
        self.session_file_sources = []
        self.session_file_modes = []
        self.session_pages = []
        self.session_page_modes = []
        self.page_counter = 0
        self.SetStatusText("No active session")
        self.output_box.Clear()
        self.output_box.Disable()

        self.update_controls()
        self.mode_tabs.SetFocus()


def main():
    # Guard against two copies running at once - a second copy would
    # silently confuse someone using a screen reader (which window is
    # which?), and two processes could also collide over the same
    # temp/output files or a local AI download in progress.
    instance_checker = wx.SingleInstanceChecker(f"ScanBox-{wx.GetUserId()}")
    if instance_checker.IsAnotherRunning():
        app = wx.App(False)
        existing_hwnd = ctypes.windll.user32.FindWindowW(None, "ScanBox")
        if existing_hwnd:
            SW_RESTORE = 9
            ctypes.windll.user32.ShowWindow(existing_hwnd, SW_RESTORE)
            ctypes.windll.user32.SetForegroundWindow(existing_hwnd)
            message = "ScanBox is already running. Switching to the existing window."
        else:
            message = (
                "ScanBox is already running. Check your taskbar for the "
                "open window."
            )
        wx.MessageBox(message, "ScanBox", wx.OK | wx.ICON_INFORMATION)
        return

    app = wx.App(False)
    frame = ScanBox()
    frame.Show()
    app.MainLoop()


if __name__ == "__main__":
    main()
