#!/bin/sh

set -eu

PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
APP_PATH="${HOME}/Library/Caches/ScanBox/dist/ScanBox.app"
LOG_PATH="${PROJECT_DIR}/scanbox-macos.log"
STARTUP_LOG_PATH="${PROJECT_DIR}/scanbox-macos-startup.log"
BUILD_LOG_PATH="${PROJECT_DIR}/macos-build.log"
PIP_LOG_PATH="${PROJECT_DIR}/macos-pip-install.log"
BUILD_PYTHON="${PROJECT_DIR}/.venv-macos-test/bin/python"
BUILD_PIP="${PROJECT_DIR}/.venv-macos-test/bin/pip"
BUILD_REVISION="48"
BUILD_STAMP="${HOME}/Library/Caches/ScanBox/build-revision"

rm -f "${STARTUP_LOG_PATH}"
: > "${STARTUP_LOG_PATH}"

if [ "$(uname -s)" != "Darwin" ]; then
  echo "ERROR: This script must be run on macOS." | tee -a "${STARTUP_LOG_PATH}" >&2
  exit 1
fi

APP_EXECUTABLE="${APP_PATH}/Contents/MacOS/ScanBox"
NEEDS_BUILD=0
if [ ! -x "${APP_EXECUTABLE}" ]; then
  NEEDS_BUILD=1
elif [ ! -f "${BUILD_STAMP}" ] || [ "$(cat "${BUILD_STAMP}")" != "${BUILD_REVISION}" ]; then
  NEEDS_BUILD=1
fi

# A matching manual revision must not hide newer source files. Rebuild when
# any input embedded in ScanBox.app is newer than the packaged executable.
for BUILD_INPUT in \
  "${PROJECT_DIR}/scanbox.py" \
  "${PROJECT_DIR}/packaging/macos/ScanBoxMacHelper.swift" \
  "${PROJECT_DIR}/packaging/ScanBox-macOS.spec" \
  "${PROJECT_DIR}/packaging/build_macos.py" \
  "${PROJECT_DIR}/requirements-macos.txt" \
  "${PROJECT_DIR}/config/vision_pack_qwen3_vl_2b.json"
do
  if [ "${BUILD_INPUT}" -nt "${APP_EXECUTABLE}" ]; then
    NEEDS_BUILD=1
    break
  fi
done

# Runs every time, not just when NEEDS_BUILD is set: a change to
# requirements-macos.txt (e.g. adding pyobjc-framework-Cocoa) does not
# bump BUILD_REVISION by itself, so gating this on NEEDS_BUILD let a
# dependency addition silently never get installed on a run that otherwise
# decided no rebuild was needed. pip is fast when everything is already
# satisfied, so running it unconditionally costs nothing on the common case.
if [ -x "${BUILD_PYTHON}" ]; then
  rm -f "${PIP_LOG_PATH}"
  echo "Installing Python dependencies. Output: ${PIP_LOG_PATH}"
  if ! "${BUILD_PIP}" install -r "${PROJECT_DIR}/requirements-macos.txt" \
      >"${PIP_LOG_PATH}" 2>&1; then
    echo "ERROR: Installing Python dependencies failed. See ${PIP_LOG_PATH}." \
      | tee -a "${STARTUP_LOG_PATH}" >&2
    exit 1
  fi
elif [ "${NEEDS_BUILD}" -eq 1 ]; then
  echo "ERROR: The existing Mac build environment was not found." \
    | tee -a "${STARTUP_LOG_PATH}" >&2
  exit 1
fi

if [ "${NEEDS_BUILD}" -eq 1 ]; then
  rm -f "${BUILD_LOG_PATH}"
  echo "Building ScanBox. Build output: ${BUILD_LOG_PATH}"
  if ! "${BUILD_PYTHON}" "${PROJECT_DIR}/packaging/build_macos.py" \
      >"${BUILD_LOG_PATH}" 2>&1; then
    echo "ERROR: The Mac build failed. See ${BUILD_LOG_PATH}." \
      | tee -a "${STARTUP_LOG_PATH}" >&2
    exit 1
  fi
  if [ ! -x "${APP_EXECUTABLE}" ]; then
    echo "ERROR: The build finished without creating ScanBox.app." \
      | tee -a "${STARTUP_LOG_PATH}" >&2
    exit 1
  fi
  printf '%s\n' "${BUILD_REVISION}" > "${BUILD_STAMP}"
fi

# A running copy keeps its original log file open and prevents the test copy
# from starting. Close it before redirecting the log into the project
# folder. Also clear any orphaned scanbox-macos-helper process left behind
# by a force-quit ScanBox (a normal quit terminates its own helper, but a
# force-quit does not) - a stray helper still holds the global keyboard
# shortcuts' registrations and makes a fresh session's own shortcuts work
# only intermittently.
pkill -x ScanBox 2>/dev/null || true
pkill -f scanbox-macos-helper 2>/dev/null || true
pkill -f llama-server 2>/dev/null || true
sleep 1

rm -f "${LOG_PATH}"
echo "Starting ScanBox."
echo "Log: ${LOG_PATH}"
echo "Startup errors: ${STARTUP_LOG_PATH}"

launchctl setenv SCANBOX_FORCE_LOGGING 1
launchctl setenv SCANBOX_LOG_FILE "${LOG_PATH}"
open -n "${APP_PATH}" >"${STARTUP_LOG_PATH}" 2>&1
sleep 1
open -a "${APP_PATH}" >"${STARTUP_LOG_PATH}" 2>&1 || true
