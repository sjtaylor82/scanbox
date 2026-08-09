import unittest

from scanbox import suggested_document_filename


class SuggestedDocumentFilenameTests(unittest.TestCase):
    def test_receipt_uses_issuer_date_and_total(self):
        self.assertEqual(
            suggested_document_filename(
                "Example Cafe\nReceipt\n9 August 2026\nTotal $18.50", "pdf"
            ),
            "Receipt - Example Cafe - 9 August 2026 - $18.50.pdf",
        )

    def test_unknown_document_uses_neutral_name(self):
        self.assertEqual(
            suggested_document_filename("unreadable fragment", "txt"),
            "Scanned document.txt",
        )

    def test_extension_can_be_left_to_save_dialog_filter(self):
        self.assertEqual(
            suggested_document_filename("Example Energy\nElectricity bill", ""),
            "Bill - Example Energy",
        )


if __name__ == "__main__":
    unittest.main()
