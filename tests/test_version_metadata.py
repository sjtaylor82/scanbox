"""Guard the version metadata screen readers announce for the active app.

ScanBox 2026.9.1 was built with an empty version resource, so JAWS had nothing
to report for Ctrl+Insert+V. These tests exercise the same lookup the screen
reader performs.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "packaging"))

import scanbox

if os.name == "nt":
    import version_resource


@unittest.skipUnless(os.name == "nt", "Windows version resources")
class VersionResourceTests(unittest.TestCase):
    def test_reads_version_strings_from_a_signed_system_binary(self):
        strings = version_resource.read_version_strings(
            Path(os.environ["SystemRoot"]) / "System32" / "kernel32.dll"
        )

        self.assertTrue(strings.get("ProductVersion"))
        self.assertTrue(strings.get("CompanyName"))

    def test_reports_a_binary_without_a_version_resource(self):
        with tempfile.TemporaryDirectory() as directory:
            plain = Path(directory) / "no-resource.exe"
            plain.write_bytes(b"MZ" + b"\0" * 512)

            self.assertEqual(version_resource.read_version_strings(plain), {})
            self.assertEqual(
                version_resource.verify_release_metadata(plain, "ScanBox", "1.0"),
                ["the executable has no readable version resource"],
            )

    def test_flags_a_version_that_does_not_match_the_release(self):
        problems = version_resource.verify_release_metadata(
            Path(os.environ["SystemRoot"]) / "System32" / "kernel32.dll",
            scanbox.APP_NAME,
            scanbox.APP_VERSION,
        )

        self.assertTrue(problems)
        self.assertTrue(any("ProductName" in problem for problem in problems))

    def test_packaging_specification_stamps_a_complete_version_resource(self):
        """Verify the spec itself, without waiting for a full release build."""
        try:
            from PyInstaller.utils.win32.versioninfo import (
                VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
                StringStruct, VarFileInfo, VarStruct,
                write_version_info_to_executable,
            )
        except ImportError:
            self.skipTest("PyInstaller is not installed")

        project = Path(scanbox.__file__).parent
        spec = (project / "packaging" / "ScanBox-Windows.spec").read_text(encoding="utf-8")
        # Run only the metadata prologue; the rest of the spec needs a build.
        prologue = spec.split("config_dir = project")[0].split(
            "project = Path(SPECPATH).parent"
        )[1]
        namespace = {
            "ast": __import__("ast"), "Path": Path, "project": project,
            "VSVersionInfo": VSVersionInfo, "FixedFileInfo": FixedFileInfo,
            "StringFileInfo": StringFileInfo, "StringTable": StringTable,
            "StringStruct": StringStruct, "VarFileInfo": VarFileInfo,
            "VarStruct": VarStruct,
        }
        exec(prologue, namespace)
        self.assertEqual(namespace["app_version"], scanbox.APP_VERSION)

        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "probe.exe"
            probe.write_bytes(Path(sys.executable).read_bytes())
            write_version_info_to_executable(str(probe), namespace["version_info"])

            self.assertEqual(
                version_resource.verify_release_metadata(
                    probe, scanbox.APP_NAME, scanbox.APP_VERSION
                ),
                [],
            )

    def test_announcement_is_the_name_and_version_and_nothing_else(self):
        self.assertEqual(
            version_resource.announcement(
                {"FileDescription": "ScanBox", "FileVersion": "2026.9.3"}
            ),
            "ScanBox version 2026.9.3",
        )
        self.assertEqual(version_resource.announcement({"FileVersion": "1.0"}), "")

    def test_rejects_publisher_fields_that_pad_the_announcement(self):
        """A licence line in the resource is spoken before the version."""
        try:
            from PyInstaller.utils.win32.versioninfo import (
                VSVersionInfo, FixedFileInfo, StringFileInfo, StringTable,
                StringStruct, VarFileInfo, VarStruct,
                write_version_info_to_executable,
            )
        except ImportError:
            self.skipTest("PyInstaller is not installed")

        padded = VSVersionInfo(
            ffi=FixedFileInfo(filevers=(1, 0, 0, 0), prodvers=(1, 0, 0, 0)),
            kids=[
                StringFileInfo([StringTable("040904B0", [
                    StringStruct("ProductName", "ScanBox"),
                    StringStruct("ProductVersion", "1.0"),
                    StringStruct("FileDescription", "ScanBox"),
                    StringStruct("FileVersion", "1.0"),
                    StringStruct("CompanyName", "ScanBox"),
                    StringStruct("LegalCopyright", "Licensed under the GNU GPL v3."),
                ])]),
                VarFileInfo([VarStruct("Translation", [0x0409, 1200])]),
            ],
        )
        with tempfile.TemporaryDirectory() as directory:
            probe = Path(directory) / "padded.exe"
            probe.write_bytes(Path(sys.executable).read_bytes())
            write_version_info_to_executable(str(probe), padded)

            problems = version_resource.verify_release_metadata(probe, "ScanBox", "1.0")

            self.assertEqual(len(problems), 2, problems)
            self.assertTrue(any("CompanyName" in problem for problem in problems))
            self.assertTrue(any("LegalCopyright" in problem for problem in problems))

    def test_release_targets_include_the_window_owning_module(self):
        """JAWS reads the module that created the window, not ScanBox.exe."""
        app_dir = Path(scanbox.__file__).parent / "dist" / "ScanBox"
        if not app_dir.is_dir():
            self.skipTest("No packaged Windows build in dist/")

        targets = version_resource.release_targets(app_dir)

        self.assertEqual(targets[0].name, "ScanBox.exe")
        self.assertTrue(
            any(t.name.startswith("_core") and t.suffix == ".pyd" for t in targets),
            f"wxPython's compiled core is missing from {targets}",
        )

    def test_every_packaged_target_announces_the_current_version(self):
        app_dir = Path(scanbox.__file__).parent / "dist" / "ScanBox"
        if not app_dir.is_dir():
            self.skipTest("No packaged Windows build in dist/")

        for target in version_resource.release_targets(app_dir):
            with self.subTest(target=target.name):
                problems = version_resource.verify_release_metadata(
                    target, scanbox.APP_NAME, scanbox.APP_VERSION
                )
                self.assertEqual(problems, [], "; ".join(problems))
                self.assertEqual(
                    version_resource.announcement(
                        version_resource.read_version_strings(target)
                    ),
                    f"{scanbox.APP_NAME} version {scanbox.APP_VERSION}",
                )


if __name__ == "__main__":
    unittest.main()
