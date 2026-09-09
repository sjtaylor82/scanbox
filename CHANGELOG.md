# Changelog

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
