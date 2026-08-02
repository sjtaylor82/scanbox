#!/bin/sh

set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_PATH="${HOME}/Library/Caches/ScanBox/dist/ScanBox.app"
LOG_PATH="${PROJECT_DIR}/scanbox-macos.log"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: This script must be run on macOS." >&2
  exit 1
fi

if [ ! -d "${APP_PATH}" ]; then
  echo "ERROR: ScanBox.app was not found at ${APP_PATH}." >&2
  echo "Run test_macos.sh first to build it, then run this script." >&2
  exit 1
fi

# test_macos.sh starts ScanBox with `nohup ... &`, so it's technically a
# background job of that shell for as long as Terminal stays open. This
# script instead opens ScanBox the same way double-clicking it in Finder
# would - through LaunchServices (`open`), fully independent of this
# Terminal window and this shell's process tree. Useful for checking
# whether the launch method itself affects anything, such as the global
# keyboard shortcuts.
#
# Also clears any orphaned scanbox-macos-helper process left behind by a
# force-quit ScanBox (a normal quit terminates its own helper, but a
# force-quit does not) - a stray helper still holds the global keyboard
# shortcuts' registrations and makes a fresh session's own shortcuts work
# only intermittently.
pkill -x ScanBox 2>/dev/null || true
pkill -f scanbox-macos-helper 2>/dev/null || true
sleep 1

rm -f "${LOG_PATH}"

echo "Opening ScanBox via Finder's own launch path (open)."
echo "Log: ${LOG_PATH}"

open \
  --env SCANBOX_FORCE_LOGGING=1 \
  --env SCANBOX_LOG_FILE="${LOG_PATH}" \
  -a "${APP_PATH}"
