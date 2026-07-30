ScanBox
=======

ScanBox is a privacy-first scanning, recognition and image description app for Windows.
Documents, photographs, screenshots, OCR results, and AI descriptions are
processed locally under the standard configuration rather than being sent to
a cloud service. ScanBox may use the internet to check GitHub for application
updates and to download optional local AI model files.

Modes
-----

1. Document OCR
   - scan a page from a Windows scanner, or import an image or PDF
   - consults local Tesseract OCR
   - if OCR confidence is low, or the page contains handwriting, ScanBox will ask a local vision model to
     transcribe the page

2. Photograph Description
   - import or scan a photo
   - asks a local vision model to describe the image
   - "Use OCR for text in photographs" grounds the description with a Tesseract text draft when confidence is high enough, so names and other visible words come through more accurately
   - descriptions can be selected and copied with standard copy commands

3. Photo Library
   - browse previously described photos, see the stored description, and open, locate, or copy the path of the original file

What you need
-------------

  Windows 10 or 11 (64-bit).

  Scanning uses Windows Image Acquisition (WIA), so any scanner with a Windows
  driver works.

  Tesseract OCR is bundled. The optional local vision model is downloaded on
  demand (see below).

Local vision model
------------------

The local vision model is optional. ScanBox works without it, but photograph
description and OCR rescue require it.

Easiest setup: open Settings (Ctrl+Comma), go to the AI tab, and choose
"Install or update local AI model". Pick a size when prompted (see below),
and it downloads, unpacks, and configures everything automatically in the
background.

Recommended model (built in)
----------------------------

  Runtime: ONNX Runtime, in-process (not a separate program)
  Model:   Florence-2, in two sizes:
             - Smaller/base  (~355MB) - quicker, less accurate
             - Larger        (~1.06GB) - slower, improved accuracy

  Either size handles both general photograph descriptions and document/
  receipt transcription with the one model. It's a fixed-prompt model, not
  an instructable chat-style one, so it always writes a short caption or
  reads text in a fixed way rather than following custom style instructions.

  Both sizes can be installed at once. Once at least one is, Settings > AI
  shows a "Photo/document model" choice at the top of the tab (one option
  per installed size); switching takes effect immediately, no restart
  needed. Only one size is active at a time - there's no per-photo choice,
  it applies to every description and transcription until changed again.
  A "Delete selected model" button underneath removes whichever size is
  currently selected from this computer, freeing up its disk space.

Saving results
--------------

Document results can be selected and copied at any time. Save Text is shown
only when "Append text to buffer for each Scan or Import" is enabled for a
multi-page session. Save Scanned Images is shown only for scanner captures;
imported images and PDFs are not duplicated. A single scan uses a standard
Save As dialog; multiple scans use collision-safe numbered filenames.
Document page headings are hidden by default and can be enabled in Settings.

Higher quality option (advanced)
---------------------------------

Want something even higher quality than Florence-2-large, and don't mind a
much bigger, slower download? Copy config\vision_pack.example.json to
config\vision_pack.json and install the local AI again. That switches to a
different, llama.cpp-based Qwen2.5-VL-3B model (about 2.8 GB) and overrides
the built-in Florence-2 pack entirely - the base/large size choice no longer
applies once this override is in place.

How install works
------------------

The Install Local AI button reads config\vision_pack.json if it exists (an
advanced override - see above), otherwise it uses the built-in Florence-2
pack matching whichever size you chose, downloading into its own
subdirectory under engines\vision so both sizes can coexist. For
distribution you can also ship engines\vision already populated.

Where your files live
----------------------

When ScanBox runs from a writable folder (for example a portable copy in
C:\ScanBox), it keeps temp, output, images, config, and the downloaded model
in that folder.

When ScanBox is installed to a read-only location (for example Program Files),
that per-user state moves to:

  %LOCALAPPDATA%\ScanBox

Bundled, read-only parts (Tesseract, the screen-reader support file, the
PDF-to-Word engine, and any pre-shipped vision pack) always stay in the install
folder.

Screen reader support
---------------------

Spoken status and results are routed to whichever Windows screen reader is
running (NVDA, JAWS, System Access, or SAPI) through accessible_output2. If no
screen reader is running, ScanBox stays silent. All controls are standard
accessible Windows widgets, and the main window opens maximized by default so
the results area has plenty of room to read.

PDF reading and Word
--------------------

When "Create a DOCX and open it in Microsoft Word" is selected, ScanBox samples the
first, middle and final PDF pages. Suitable PDFs use the bundled PDF2Word
engine. Lower-confidence PDFs are read page by page with Tesseract and the
local document model as needed, then written to a DOCX and opened in Word.
The setting is disabled when Microsoft Word cannot be found.

When "Show PDF reading in ScanBox" is selected, ScanBox does not create a DOCX. Selectable
PDF text is extracted directly; scanned pages use Tesseract or the local
document model according to the 75% confidence threshold. The result is shown
directly in the results area.

See also
--------

  config\vision_command.example.txt
  config\vision_pack.example.json
  config\vision_pack_florence2_base.json
  config\vision_pack_florence2_large.json

Privacy
-------

Document scanning, OCR, screen analysis, and photograph description are
performed on this computer. The built-in vision model runs locally in-process
through ONNX Runtime; an advanced override pack (see "Higher quality option")
runs locally through llama.cpp.

An internet connection is used in the following circumstances:

  - ScanBox checks the official GitHub repository for a newer release at
    startup when "Check for updates on startup" is enabled. This setting is
    enabled by default and can be disabled under Settings > General. The check
    sends standard web-request information and the installed ScanBox version;
    it does not upload documents, images, screenshots, or recognised text.
  - Installing or updating a local AI model downloads the required model and
    runtime files from their published sources.
  - An advanced custom AI command can be configured to call an online service.
    In that case, the privacy and data-retention practices of the selected
    provider apply. Online AI processing is not enabled by default.
