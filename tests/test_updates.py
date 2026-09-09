import os
import tempfile
import unittest
import zipfile

import scanbox


class UpdateTests(unittest.TestCase):
    def test_selects_current_platform_release_asset(self):
        manifest = {
            "html_url": "https://github.com/example/releases/tag/v1",
            "assets": [
                {"name": "ScanBox-1-macOS.zip", "browser_download_url": "https://example/mac"},
                {"name": "ScanBox-1-Windows-x64.zip", "browser_download_url": "https://example/windows"},
            ],
        }

        self.assertEqual(
            scanbox._release_asset_url(manifest, "win32"),
            "https://example/windows",
        )
        self.assertEqual(
            scanbox._release_asset_url(manifest, "darwin"),
            "https://example/mac",
        )

    def test_prepares_valid_windows_payload(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "update.zip")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ScanBox/ScanBox.exe", b"test")

            payload = scanbox._prepare_update_payload(archive, "win32")

            self.assertTrue(os.path.isfile(os.path.join(payload, "ScanBox.exe")))

    def test_rejects_unsafe_update_path(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = os.path.join(directory, "update.zip")
            with zipfile.ZipFile(archive, "w") as bundle:
                bundle.writestr("ScanBox/ScanBox.exe", b"test")
                bundle.writestr("../outside.txt", b"unsafe")

            with self.assertRaisesRegex(ValueError, "unsafe path"):
                scanbox._prepare_update_payload(archive, "win32")


if __name__ == "__main__":
    unittest.main()
