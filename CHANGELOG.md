# Changelog

## 2026.9.5

- Add a saved default-camera selector to Settings, used by document capture,
  photo description, and FaceAlign.
- Let users find local AI models at a chosen private IP address and port, with
  an optional API key for authenticated OpenAI-compatible services.
- Keep text returned by a local AI service when it reaches its output-token
  limit, and clearly mark that text as potentially incomplete.

## 2026.9.4

- Asking Windows for the active application's version now answers
  "ScanBox version <number>". A screen reader reads the file that created the
  focused window, which for ScanBox is wxPython's compiled core rather than
  ScanBox.exe, and that file carried no version information at all.
- Refuse to finish a Windows build unless every file that could be asked
  announces exactly "ScanBox version <number>", and refuse to build a macOS
  release whose version disagrees with the release tag.
- Keep only the most recent retained copy of a replaced build. Earlier copies
  and abandoned staging folders are removed at startup, instead of holding
  roughly 300 MB per update indefinitely.
- Report the graphics processor a description actually ran on, rather than
  working out afresh which one would be chosen now. The two can disagree, and
  this line is how you check whether your graphics card is being used.
- Choose a graphics card that can hold the model. Preferring a dedicated card
  by name alone could send the model to a small one in preference to an
  integrated processor with far more memory available.
- Recognise a Vulkan graphics processor running out of memory, so Windows
  falls back to the processor automatically. Only the macOS wording was
  recognised before, leaving Windows with no automatic retry.
- Record why the image model failed to start instead of discarding its
  diagnostics, so running out of graphics memory is reported rather than
  appearing as a model that never became ready.
- Rank Intel's dedicated Arc cards above integrated graphics.
- Restart ScanBox with a visible window after an update.
- Take the macOS bundle version from the application source instead of a
  separate copy in the packaging file, which had let the bundle ship under the
  previous release's number.
- macOS is rebuilt as 2026.9.4; its update mechanism is unchanged.

## 2026.9.3

- Fixed the Windows portable updater aborting when ScanBox had already exited.
- Prepare the replacement before closing ScanBox, retain the previous
  application files, and attempt rollback if replacement fails.
- Write updater progress and errors to `update.log` in the portable data folder
  (normally beside `ScanBox.exe`). Preparation errors leave ScanBox open.
- Retain settings, downloaded AI models, images and output during updates.
- macOS is rebuilt as 2026.9.3; its update mechanism is unchanged.

## 2026.9.2

- Added Windows executable product/version metadata for JAWS's application
  version command.
- Fixed external AI OCR truncation handling and TIFF image uploads. Screen
  OCR now uses the selected local service first, with native OCR fallback.
- Fixed scanner discovery resetting the Ask me each time selection.
- Fixed Qwen installations after the upstream llama.cpp latest release stopped
  publishing runtime archives. ScanBox now uses a pinned, tested runtime.
- Added automatic startup repair when Qwen is installed without its runner, or
  when an older Windows installation has only the CPU runner. Existing model
  files are retained, and the normal accessible download progress is shown.
- Added Settings > AI discovery for Ollama, LM Studio and vLLM services running
  on this computer. Every model returned by every detected service is listed,
  and the selected service can handle scans, camera captures, screenshots,
  document OCR, descriptions and image questions. ScanBox connects only to a
  service on the local computer.
- Moved scanner selection to Settings > Scanner. Users can save any detected
  scanner or choose Ask me each time; scan commands no longer ask when a saved
  scanner is available.
- Improved support for Freedom Scientific PEARL and other DirectShow bridge
  cameras. ScanBox now opens the camera before the countdown, keeps it open
  between captures, waits for it to settle and ignores blank startup frames.

## 2026.9.1

- Kept the Results area keyboard accessible during Control+backslash and
  Control+Shift+backslash processing. It now displays `Processing...`
  immediately and replaces that placeholder with the completed result.
- Added Help > Local AI Acceleration, reporting the active local model and the
  graphics processor actually used by the current or most recent description.
- Added automatic Windows Vulkan GPU selection, preferring a recognisable
  dedicated GPU over integrated graphics and retaining automatic CPU fallback.
- Made closing ScanBox authoritative during processing: active work is
  cancelled, the application exits, and temporary inputs are cleaned safely at
  the next startup instead of blocking the user with a busy dialog.
- Added in-application portable updates. Accepted GitHub releases are downloaded
  and installed while retaining settings and previously downloaded AI models.
- Improved browser PDF workflow responsiveness and Windows browser integration.

## 2026.9.0

- First public Windows release and macOS test preview.
- Added three explicit PDF recognition choices: no OCR, OCR only pages with no
  selectable text, or OCR every page. No OCR is the default and preserves
  table-aware Word conversion.
- Fixed a major apparent hang after reading very large PDFs by preventing the
  entire document from being sent to the screen reader as a completion
  announcement.
- Fixed missing FaceAlign detector data in the packaged Windows application.
