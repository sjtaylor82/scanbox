ScanBox
=======

ScanBox 2026.9.0 is a privacy-first scanning, recognition, and image-description
app for 64-bit Windows 10 and 11. A macOS 14-or-later test preview is also
available.
Documents, photographs, screenshots, OCR results, and AI descriptions are
processed locally under the standard configuration rather than being sent to
a cloud service. ScanBox uses the internet to check GitHub for application
updates and to download optional local AI model files.

Tabs
----

1. Scan
   - scan a document or photograph with a Windows scanner
   - on Windows, use a USB camera through the separate document-camera and
     photo-camera commands, even when no flatbed scanner is connected
   - on macOS, scan directly from an ImageCaptureCore flatbed scanner or use a
     supported camera through the separate camera commands
   - document captures use local OCR and can fall back to the local vision
     model for difficult text or handwriting
   - photograph captures use the local vision model for description
   - when one camera is available it is used immediately; when several are
     available ScanBox presents a keyboard-accessible camera choice
   - delayed capture runs quietly and can be stopped from the main window; fixed repeated
     capture and continuous capture with a Stop Camera Capture command are
     available
   - FaceAlign gives local, two-second face-position guidance after the user
     chooses a detected camera from a labelled camera list, and continues
     until the user cancels it

2. Import
   - import a document image, PDF, photograph, or batch of photographs
   - press Control+Shift+hyphen while viewing a public HTTPS PDF in a supported
     browser to import it directly
   - OCR Document upon import selects native OCR for image-only PDF pages;
     without it, pages that lack selectable text are still read automatically
   - leave OCR Document upon import off for a selectable-text PDF when table-aware conversion
     is preferred

3. Photo Library
   - browse previously described photos and see the stored description
   - open the original, open its containing folder, copy its path, ask the
     installed model a follow-up question, or remove the stored description

What you need
-------------

  Windows 10 or 11 (64-bit). macOS 14 or later is currently a test preview;
  signing, notarisation, and final hardware and VoiceOver testing remain
  release work.

  Windows scanner capture uses Windows Image Acquisition (WIA), so a scanner
  with a WIA driver works. USB/UVC cameras use OpenCV's Windows camera backend
  and do not need to appear as WIA scanners. Scanner and camera discovery are
  separate, so one device type cannot take precedence over the other.
  A front-facing camera can describe objects held in view. It can also attempt
  document capture, although a stable USB document camera normally gives OCR a
  clearer, more squarely positioned page.
  Camera descriptions remain temporary until the captured images are saved;
  only then are the saved paths and descriptions added to the Photo Library.

  On macOS, Scan and Read Document and Scan and Save Images discover
  conventional scanners through ImageCaptureCore and scan from the selected
  device directly into ScanBox.
  Camera capture remains separate. Allow ScanBox camera access in System
  Settings > Privacy & Security > Camera when using a camera.

  Minimizing the macOS preview leaves its window in the Dock. The Windows
  notification-area option is not shown on macOS.

  The macOS test build mirrors the Windows active-window shortcuts:
  Control+backslash describes the frontmost application's foremost window,
  Control+Shift+backslash reads its text, Control+Shift+/ asks Qwen a question
  about it, and Control+Option+backslash toggles ScanBox. macOS requires Screen
  Recording permission under System Settings > Privacy & Security for window
  capture; native shortcut registration does not require Accessibility
  permission. Importing the current browser PDF does require Accessibility on
  macOS so ScanBox can read the foreground browser's address field. Reopen
  ScanBox after granting either permission.

  Text recognition uses Windows OCR on Windows and Apple Vision on macOS. The
  optional local vision model is downloaded on demand (see below).

Local vision model
------------------

The local vision model is optional. ScanBox works without it, but photograph
description and OCR rescue require it.

Easiest setup: open Settings (Ctrl+Comma on Windows or Command+Comma on macOS),
go to the AI tab, choose an image-description model, then choose "Install or
update local AI model". ScanBox downloads and configures it in the background.

Available models
----------------

  Windows offers two local choices: the smaller, quicker Florence-2 Base
  (about 355 MB) and the larger, more accurate Qwen3-VL 2B (about 2.28 GB).
  The current macOS preview offers Qwen3-VL 2B only. Settings identifies which
  models are installed. Photo descriptions include the selected model name
  and processing time to support fair testing.

  On Windows, Florence-2 Base remains the difficult-document transcription
  fallback; the selected model changes image descriptions only. macOS uses
  Apple Vision for native OCR and Qwen for local visual description and
  questions.
  On Windows and macOS, substantial native OCR is shown separately beneath the
  description. It is not supplied to Qwen, so imperfect OCR cannot influence
  the visual description.
  On computers with at least 8 GB of memory, Qwen is loaded quietly at launch
  and kept ready until ScanBox closes. This substantially reduces the wait for
  later descriptions. Computers below 8 GB load it only when needed.

Saving results
--------------

Document results can be selected and copied at any time. Save Text is shown
only when "Append text to buffer for each Scan or Import" is enabled for a
multi-page session. Scan and Read Document does not offer image export.
Use Scan and Save Images for a separate, non-reading workflow that saves one
page as JPEG or collects multiple pages into one PDF. ScanBox quietly tests
four physical rotations with native OCR and deterministic scoring, then
reports whether each page is upright, needs rotation, or could not be
determined. It offers to rotate the whole page before export when appropriate.
Imported images and PDFs are not duplicated. Document page headings are hidden
by default and can be enabled in Settings.

How install works
------------------

The Install or update local AI model button downloads the model selected in
Settings. Qwen3-VL also receives ScanBox's private local multimodal runner.
Windows prefers the hardware-accelerated Vulkan runner and retains a CPU
fallback; macOS uses the native runner. Users are not asked to configure
commands or executable paths. A distribution can also ship `engines\vision`
already populated.

Running from source
-------------------

Use a 64-bit Python environment. On Windows, install `requirements.txt`, then
run:

  python scanbox.py

To create an isolated, folder-based Windows build, run:

  python packaging/build_windows.py

The build script creates a private environment under `temp`, installs the
declared runtime and packaging dependencies, and writes the application to
`dist\ScanBox`.

On macOS, install `requirements-macos.txt`. The native helper and application
bundle must be built on a Mac with Xcode command-line tools available:

  python packaging/build_macos.py

For the existing macOS test environment, `bash test_macos.sh` installs updated
requirements, rebuilds when inputs have changed, and opens the cached app
bundle. See `packaging/README-macOS.md` for platform testing and distribution
requirements.

Where your files live
----------------------

When ScanBox runs from a writable folder (for example a portable copy in
C:\ScanBox), it keeps temp, output, images, config, and the downloaded model
in that folder.

When ScanBox is installed to a read-only location (for example Program Files),
that per-user state moves to:

  %LOCALAPPDATA%\ScanBox

For a packaged macOS build, per-user state is stored in:

  ~/Library/Application Support/ScanBox

Bundled, read-only resources and any pre-shipped vision pack always stay in
the install folder. Windows distributions may additionally bundle screen-reader
support and the PDF-to-Word engine.

Screen reader support
---------------------

On Windows, spoken status and results are routed to the running screen reader
(NVDA, JAWS, System Access, or SAPI) through accessible_output2. The macOS
test build uses standard wxWidgets controls for navigation and native AppKit
announcements for VoiceOver speech and braille. The main window opens maximized
by default so the results area has plenty of room to read.

PDF reading and Word
--------------------

On Windows, when "Create a DOCX and open it in Microsoft Word" is selected,
PDFs with selectable text use the bundled PDF2Word engine. Scanned PDFs are
read page by page with Windows OCR and written to a DOCX. The choice is
disabled when Microsoft Word cannot be found. PDF-to-Word is not available in
the current macOS preview.

When "Show PDF reading in ScanBox" is selected, ScanBox does not create a DOCX. Selectable
PDF text is extracted directly; scanned pages use native operating-system OCR.
The result is shown directly in the results area.

Model manifests
---------------

  config\vision_pack_florence2_base.json
  config\vision_pack_florence2_base_macos.json
  config\vision_pack_qwen3_vl_2b.json

Privacy
-------

Document scanning, OCR, screen analysis, and photograph description are
performed on this computer. Florence-2 runs locally in-process through ONNX
Runtime. Qwen3-VL runs through a private loopback service started and stopped
by ScanBox. Windows document and screen OCR use Windows OCR. macOS document
and screen OCR use Apple Vision.

An internet connection is used in the following circumstances:

  - ScanBox checks the official GitHub repository for a newer release at
    startup when "Check for updates on startup" is enabled. This setting is
    enabled by default and can be disabled under Settings > General. The check
    sends standard web-request information and the installed ScanBox version;
    it does not upload documents, images, screenshots, or recognised text.
  - Installing or updating a local AI model downloads the required model files
    from their published source.
  - Import PDF from Current Browser Tab downloads the public HTTPS PDF shown in
    the foreground browser. ScanBox does not inherit browser cookies or login
    sessions, and rejects non-PDF responses and files larger than 100 MB.
