#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
VENV_DIR="${PROJECT_DIR}/.venv-macos-test"
APP_PATH="${PROJECT_DIR}/dist/ScanBox.app"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

if [[ "$(uname -s)" != "Darwin" ]]; then
  fail "This script must be run on macOS."
fi

MAC_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if (( MAC_MAJOR < 14 )); then
  fail "ScanBox currently requires macOS 14 or later."
fi

command -v xcrun >/dev/null 2>&1 || fail \
  "Install Apple's command-line tools with: xcode-select --install"
command -v python3 >/dev/null 2>&1 || fail "Python 3 was not found."

cd "${PROJECT_DIR}"

if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
  echo "Creating an isolated Python environment..."
  python3 -m venv "${VENV_DIR}"
fi

echo "Installing ScanBox build dependencies..."
"${VENV_DIR}/bin/python" -m pip install --upgrade pip
"${VENV_DIR}/bin/python" -m pip install -r requirements-macos.txt

echo "Building ScanBox.app..."
"${VENV_DIR}/bin/python" packaging/build_macos.py

[[ -d "${APP_PATH}" ]] || fail "The build did not create ${APP_PATH}."

HELPER_PATH="${APP_PATH}/Contents/Frameworks/macos/scanbox-macos-helper"
FACE_CASCADE_PATH="$(find "${APP_PATH}/Contents" -type f -name haarcascade_frontalface_default.xml -print -quit)"
SHUTTER_PATH="$(find "${APP_PATH}/Contents" -type f -iname shutter.wav -print -quit)"

if [[ ! -x "${HELPER_PATH}" ]]; then
  HELPER_PATH="$(find "${APP_PATH}/Contents" -type f -name scanbox-macos-helper -print -quit)"
fi
[[ -n "${HELPER_PATH}" && -x "${HELPER_PATH}" ]] || fail \
  "The native hotkey/ScreenCaptureKit helper is missing from the app."
[[ -n "${FACE_CASCADE_PATH}" && -f "${FACE_CASCADE_PATH}" ]] || fail \
  "The bundled FaceAlign detector is missing from the app."
[[ -n "${SHUTTER_PATH}" && -f "${SHUTTER_PATH}" ]] || fail \
  "The bundled shutter sound is missing from the app."

echo "Checking ImageCaptureCore scanner discovery..."
"${HELPER_PATH}" list-scanners

PLIST_PATH="${APP_PATH}/Contents/Info.plist"
[[ -f "${PLIST_PATH}" ]] || fail "Info.plist is missing."
plutil -lint "${PLIST_PATH}"

BUNDLE_ID="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleIdentifier' "${PLIST_PATH}")"
MINIMUM_MACOS="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "${PLIST_PATH}")"
CAMERA_MESSAGE="$(/usr/libexec/PlistBuddy -c 'Print :NSCameraUsageDescription' "${PLIST_PATH}")"

[[ "${BUNDLE_ID}" == "au.com.scanbox.ScanBox" ]] || fail \
  "Unexpected bundle identifier: ${BUNDLE_ID}"
[[ "${MINIMUM_MACOS}" == "14.0" ]] || fail \
  "Unexpected minimum macOS version: ${MINIMUM_MACOS}"
[[ -n "${CAMERA_MESSAGE}" ]] || fail "The camera usage description is empty."

echo
echo "Build checks passed."
echo "App: ${APP_PATH}"
echo "Architecture: $(uname -m)"
echo
echo "The app will now open. Test in this order:"
echo "  1. Choose Scan Document and scan from a connected flatbed scanner."
echo "  2. If available, capture a document with a USB document camera."
echo "  3. Take a photo with a supported camera."
echo "  4. If several cameras are present, confirm the camera chooser appears."
echo "  5. Confirm the shutter sound plays without a spoken wait message."
echo "  6. Test that delayed capture remains quiet and can be stopped."
echo "  7. Test fixed repeated capture, then continuous capture and Stop."
echo "  8. Confirm camera descriptions enter the library only after Save Images."
echo "  9. Run FaceAlign with a front-facing camera and then stop it."
echo " 10. Import a document, PDF, photo, and photo batch on the Import tab."
echo " 11. Confirm the Photo Library can open and reveal a stored photo."
echo " 12. From Safari or Mail, press Control+backslash for description."
echo " 13. Press Control+Shift+backslash for OCR."
echo " 14. Confirm both screen shortcuts play the shutter sound."
echo " 15. Press Control+Option+backslash to toggle ScanBox."
echo " 16. Grant Screen Recording when asked, quit ScanBox, reopen it,"
echo "     and repeat steps 12-15."
echo " 17. With VoiceOver running, confirm status and result announcements."
echo " 18. Install Florence-2 Base, cancel one attempted download, then complete it."
echo " 19. Import a photograph and use camera description with Florence selected."
echo " 20. Install Qwen3-VL 2B, describe a photograph, and use Ask About Photo."
echo " 21. Press Control+Shift+/ in Safari or Mail and ask about the active window."
echo " 22. Minimize, restore, hide and restore ScanBox; confirm VoiceOver focus returns."
echo " 23. Quit after using Qwen and confirm no ScanBox model process remains."
echo
echo "If a shortcut or capture fails, enable diagnostic logging in Settings"
echo "and attach the scanbox.log file from ScanBox's output folder."

open "${APP_PATH}"
