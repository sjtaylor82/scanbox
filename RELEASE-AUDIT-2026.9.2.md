### ScanBox 2026.9.2 release audit — 10 September 2026

Updated after fixes: all four reproduced source defects below are resolved.
The historical findings are retained as the audit record; their line numbers
refer to the pre-fix source. Release validation is still pending: the existing
distribution is 2026.9.1 and has not been replaced or published.

### Fix verification

- Both providers receive RGB PNG image bytes; TIFF fixtures verified the media
  type, dimensions and pixels, and that source files remain unchanged.
- Transcription defaults to 8192 output tokens. OpenAI-compatible `length`
  completions and Ollama `length`/unfinished responses are rejected, allowing
  document and screen OCR to use native fallback instead of partial text.
- Explicit screen OCR uses the chosen service before native OCR; cancellation
  does not start fallback. Supplemental OCR attached to descriptions retains
  the existing native behavior.
- Discovery preserves both Ask me each time and a saved scanner, including
  when that scanner is disconnected.
- All 46 automated tests pass, including new regression cases. Compilation
  and whitespace checks pass. Windows reads ScanBox / 2026.9.2 from a temporary
  metadata probe generated using the actual packaging specification. The probe
  was removed; it was not a release build.
- APP_VERSION already contained 2026.9.2 before these fixes and was not changed.

### Findings

1. **P1 — External document OCR silently accepts truncated output.** `scanbox.py:941` limits OpenAI-compatible responses to the supplied token budget, which defaults to 512 for transcription at line 2550. Lines 960–966 discard the completion reason, and `process_document` accepts the partial text at lines 7390–7395. A simulated service response with `finish_reason: "length"` was returned as the complete document without native OCR fallback. Longer pages can silently lose their remaining text. Use a transcription-appropriate budget and detect incomplete generation, retrying or reporting/falling back instead of accepting partial output. Check Ollama's completion reason too.

2. **P1 — Scanner TIFFs are sent as JPEG without conversion.** `scanbox.py:928` assigns `image/jpeg` to every non-PNG input, but lines 911–912 base64-encode the original bytes. WIA scans are saved as TIFF at lines 5624–5629 and passed directly to document OCR. A real TIFF fixture produced a `data:image/jpeg` URL containing TIFF bytes. Services that validate the media type or do not support TIFF will reject these scans and trigger native OCR fallback instead of the chosen model. Convert supported input images to PNG or JPEG before constructing either provider's request.

3. **P2 — Scanner discovery restores a saved scanner after the user chooses “Ask me each time.”** In `finish_scanner_discovery`, `scanbox.py:7630`, selection zero resolves to `saved_scanner_id`. Executing the actual callback with selection zero and a saved scanner changed the selection back to one. This occurs on an explicit refresh and can also race with the initial background discovery. Preserve selection zero as an empty scanner ID.

4. **P2 — Screen OCR bypasses the selected external AI.** `ScanBox._read_screen_text`, `scanbox.py:4907`, always runs native OCR first and returns as soon as it finds five words. With external AI selected and native OCR returning six words, the selected model was never called. Route explicit screen OCR through the selected provider first, consistent with the new document OCR behavior and README promise, with native fallback on failure.

### Artifact readiness

- Reading the embedded `scanbox` code object in `dist/ScanBox/ScanBox.exe` found `2026.9.1`, not `2026.9.2`.
- Windows FileVersionInfo reported empty ProductName, ProductVersion and FileVersion for that executable. The new packaging metadata is not yet in the distributable.
- No 2026.9.2 release ZIP was found in the inspected project root or dist directory. Build a fresh candidate after resolving the findings and validate that exact candidate before publishing.

### Checks completed

- `python -m unittest discover -s tests -v`: **41 passed**.
- `python -m compileall -q scanbox.py pdf_to_word.py get_active_tab_url.py packaging tests`: passed.
- `git diff --check`: passed; Git emitted line-ending conversion notices only.
- Four targeted probes reproduced the findings above. Service responses and GUI controls were simulated; the TIFF fixture used actual image bytes. No images were sent to a real AI service.
- Reviewed the changed application paths, Windows packaging, release notes, and README/manual changes.
- Confirmed the pinned llama.cpp release page lists Windows CPU and Vulkan downloads: https://github.com/ggml-org/llama.cpp/releases/tag/b10216 . This was a listing check, not an archive download or inference test.

### Validation still needed before release

After fixing the findings, add regression coverage and build a fresh Windows candidate. Test installation/update from 2026.9.1 with settings and models retained; Qwen runtime repair and CPU fallback; real Ollama and OpenAI-compatible vision OCR on a long page and a TIFF; scanner selection; and repeated PEARL captures. Verify the JAWS version hotkey and keyboard navigation against that packaged executable. Real hardware, screen-reader behavior, live model inference, and macOS operation were not exercised in this audit.
