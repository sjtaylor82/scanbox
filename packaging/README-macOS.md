# Building ScanBox for macOS

Run these steps on a Mac. A Windows machine cannot produce or sign a native
macOS app bundle. The current ScreenCaptureKit helper targets macOS 14 or
later.

For a complete local test build, run:

`bash packaging/test_macos.sh`

The script creates an isolated environment, installs build dependencies,
builds and checks the app, prints the manual test sequence, and opens ScanBox.

1. Create and activate a Python virtual environment.
2. Install `requirements-macos.txt`.
3. Run `python packaging/build_macos.py`. This compiles the native
   ScreenCaptureKit and ImageCaptureCore helper, then builds the app bundle.
4. Open `dist/ScanBox.app`.

The native helper scans directly from compatible flatbed scanners through
ImageCaptureCore. Scanner capture and camera capture are separate commands, so
a camera cannot take precedence over a scanner. The bundle declares why camera
access is required and carries the camera entitlement. The first active-window capture requires Screen Recording
permission under System Settings > Privacy & Security. Native Carbon hotkey
registration does not require Accessibility permission.

The shortcuts mirror Windows: Control+backslash describes the active window,
Control+Shift+backslash reads its text, Control+Shift+/ asks Qwen a question
about it, and Control+Option+backslash toggles ScanBox. After granting Screen
Recording permission, quit and reopen ScanBox before testing again.

Before distribution, build on each supported Mac architecture (or deliberately
produce a universal binary), test with VoiceOver, a compatible flatbed scanner,
and camera hardware, sign with a Developer ID Application certificate, and
submit the signed bundle for Apple notarisation.
