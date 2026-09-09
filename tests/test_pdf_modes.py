import unittest
from unittest import mock

import scanbox


class _Pdf:
    page_count = 1

    def __init__(self, page):
        self.page = page

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def load_page(self, index):
        return self.page


class PdfModeTests(unittest.TestCase):
    def test_no_ocr_is_the_default_pdf_mode(self):
        self.assertEqual(scanbox.DEFAULT_APP_SETTINGS["pdf_ocr_mode"], "none")

    def test_pdf_completion_does_not_announce_entire_document(self):
        frame = mock.Mock()
        frame.app_settings = {
            "show_page_headings": False,
            "render_converted_in_window": True,
        }
        frame.page_counter = 0
        frame.session_pages = []
        frame.session_page_modes = []
        pages = ["A" * 300_000]

        with mock.patch.object(scanbox, "announce") as speak:
            scanbox.ScanBox._finish_pdf_text(frame, pages)

        frame.append_output.assert_called_once_with(pages[0] + "\n\n")
        frame.output_box.SetFocusFromKbd.assert_called_once_with()
        speak.assert_called_once_with("PDF reading completed.")

    def test_ocr_off_does_not_recognize_image_only_page(self):
        page = mock.Mock()
        page.get_text.return_value = ""
        frame = mock.Mock()
        frame.render_pdf_page.return_value = "rendered.png"
        frame.process_document.return_value = "ocr text"

        with mock.patch.object(scanbox.fitz, "open", return_value=_Pdf(page)):
            with self.assertRaises(scanbox.PdfNeedsOcrError):
                scanbox.ScanBox.read_pdf_pages(frame, "document.pdf")

        frame.process_document.assert_not_called()
        frame.render_pdf_page.assert_not_called()

    def test_ocr_on_preserves_existing_selectable_text(self):
        page = mock.Mock()
        page.get_text.return_value = "embedded text"
        frame = mock.Mock()
        frame.render_pdf_page.return_value = "rendered.png"

        with mock.patch.object(scanbox.fitz, "open", return_value=_Pdf(page)), \
                mock.patch.object(scanbox, "windows_ocr", return_value="ocr text") \
                as recognize, \
                mock.patch.object(scanbox, "_remove_quietly"):
            pages = scanbox.ScanBox.read_pdf_pages_with_native_ocr(
                frame, "document.pdf"
            )

        self.assertEqual(pages, ["embedded text"])
        recognize.assert_not_called()
        frame.render_pdf_page.assert_not_called()

    def test_ocr_on_recognizes_page_without_selectable_text(self):
        page = mock.Mock()
        page.get_text.return_value = ""
        frame = mock.Mock()
        frame.render_pdf_page.return_value = "rendered.png"

        with mock.patch.object(scanbox.fitz, "open", return_value=_Pdf(page)), \
                mock.patch.object(scanbox, "windows_ocr", return_value="ocr text") \
                as recognize, \
                mock.patch.object(scanbox, "_remove_quietly"):
            pages = scanbox.ScanBox.read_pdf_pages_with_native_ocr(
                frame, "document.pdf"
            )

        self.assertEqual(pages, ["ocr text"])
        recognize.assert_called_once_with("rendered.png")

    def test_all_pages_mode_recognizes_page_with_selectable_text(self):
        page = mock.Mock()
        page.get_text.return_value = "embedded text"
        frame = mock.Mock()
        frame.render_pdf_page.return_value = "rendered.png"

        with mock.patch.object(scanbox.fitz, "open", return_value=_Pdf(page)), \
                mock.patch.object(scanbox, "windows_ocr", return_value="ocr text") \
                as recognize, \
                mock.patch.object(scanbox, "_remove_quietly"):
            pages = scanbox.ScanBox.read_pdf_pages_with_native_ocr(
                frame, "document.pdf", all_pages=True
            )

        self.assertEqual(pages, ["ocr text"])
        recognize.assert_called_once_with("rendered.png")


if __name__ == "__main__":
    unittest.main()
