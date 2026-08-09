# ScanBox for macOS

The macOS version will retain ScanBox's existing OCR, image correction,
PDF-reading, local-AI, saving, and Photo Library code. Platform-specific
services are being replaced incrementally on the `macos-port` branch.

## Initial scope

- Import and process document images and PDFs.
- Import, scan, describe, and batch-process photographs.
- Take photographs with the default Mac camera and capture documents with a
  repositionable USB document camera.
- Detect, crop, straighten, and recognise camera-captured pages with the
  existing pipeline.
- Run Qwen3-VL 2B locally through ScanBox's private multimodal runner.
- Save text, PDF, DOCX, and image results.
- Retain the Photo Library, Help, About, update checks, and settings.
- Support VoiceOver announcements and accessible keyboard navigation.
- Add active-window description and OCR through ScreenCaptureKit.
- Add native global shortcuts and menu-bar background operation.
- Scan directly from conventional flatbed scanners through ImageCaptureCore.

## Deliberately deferred

- Opening converted PDFs in Microsoft Word is Windows-only. The control remains
  unavailable on macOS for the initial release.

## Current implementation status

- Windows-only imports and process flags are guarded.
- User data has a macOS Application Support location for packaged builds.
- Default file and folder opening supports Finder.
- PDF-to-Word capability is disabled outside Windows.
- Windows WIA scanning remains unchanged.
- On macOS, the document scanning workflows discover ImageCaptureCore scanners, let the user
  select one when necessary, selects its flatbed, and returns the scanned file
  to the existing OCR pipeline. Camera capture remains a separate workflow.
- The Mac port follows the shared Scan, Import, and Photo Library tab layout.
  Camera capture stays on Scan; file/PDF/batch selection and the
  OCR Document upon import control stay on Import.
- Windows hotkey cleanup and notification-area shortcut labels are now
  platform guarded.
- Ordinary minimized windows remain discoverable through Alt+Tab and the
  taskbar or Dock on both platforms. Only the global toggle shortcut hides and
  restores the app.
- A PyInstaller app-bundle specification and camera entitlement are available
  under `packaging/`.
- macOS text recognition uses Apple Vision, so users do not install separate
  OCR tools. Final bundle signing and OCR testing remain release work.
- A native Swift helper identifies the frontmost application's foremost
  normal window and captures it through ScreenCaptureKit. Global shortcuts
  mirror Windows and route the image into the existing description/OCR
  workers.
- Existing status and result announcements are sent through AppKit's native
  VoiceOver announcement notification for speech and braille without an
  additional permission.
- Mac testing of Screen Recording consent, native shortcut registration,
  Safari/Mail window selection, multiple displays, and VoiceOver focus remains
  outstanding.
- ImageCaptureCore scanner hardware testing, signing, and notarisation remain
  outstanding.
- `test_macos.sh` produces a repeatable local test build, verifies
  the helper, FaceAlign data, shutter sound, and permission metadata, then
  opens the app with a focused manual test checklist.
