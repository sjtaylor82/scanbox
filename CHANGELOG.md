# Changelog

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
