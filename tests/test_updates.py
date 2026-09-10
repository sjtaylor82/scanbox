import os
import tempfile
import unittest
import zipfile
import subprocess
import ctypes
import time
from pathlib import Path
from unittest import mock

import scanbox


class UpdateTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows updater integration")
    def test_python_handoff_waits_for_real_helper_readiness(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "app"
            payload = root / "payload"
            install.mkdir()
            (install / "ScanBox.exe").write_text("old")
            (payload / "_internal").mkdir(parents=True)
            (payload / "ScanBox.exe").write_text("new")
            parent = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 60"])
            popen = subprocess.Popen
            helpers = []

            def launch(command, **kwargs):
                helper = popen(command + ["-NoRestart"], **kwargs)
                helpers.append(helper)
                return helper

            try:
                with (
                    mock.patch.object(scanbox.sys, "frozen", True, create=True),
                    mock.patch.object(scanbox, "BASE", str(install)),
                    mock.patch.object(scanbox, "DATA_DIR", str(root)),
                    mock.patch.object(scanbox, "RESOURCE_BASE", str(Path(scanbox.__file__).parent / "packaging")),
                    mock.patch.object(scanbox.os, "getpid", return_value=parent.pid),
                    mock.patch.object(scanbox.subprocess, "Popen", side_effect=launch),
                    mock.patch.object(scanbox.tempfile, "gettempdir", return_value=str(root)),
                ):
                    scanbox._launch_portable_updater(str(payload))
                self.assertEqual((install / "ScanBox.exe").read_text(), "old")
                parent.terminate()
                parent.wait(timeout=10)
                self.assertEqual(helpers[0].wait(timeout=20), 0)
                self.assertEqual((install / "ScanBox.exe").read_text(), "new")
                self.assertIn("Update completed", (root / "update.log").read_text(encoding="utf-8-sig"))
            finally:
                for process in [parent] + helpers:
                    if process.poll() is None:
                        process.terminate()
                        process.wait(timeout=10)

    @unittest.skipUnless(os.name == "nt", "Windows updater integration")
    def test_portable_helper_handles_exited_parent_and_preserves_user_data(self):
        self._run_helper_case(valid=True)

    @unittest.skipUnless(os.name == "nt", "Windows updater integration")
    def test_portable_helper_keeps_app_on_preparation_failure(self):
        self._run_helper_case(valid=False)

    @unittest.skipUnless(os.name == "nt", "Windows updater integration")
    def test_portable_helper_rolls_back_when_dependency_is_locked(self):
        self._run_helper_case(valid=True, locked=True)

    @unittest.skipUnless(os.name == "nt", "Windows updater integration")
    def test_portable_helper_waits_until_running_parent_exits(self):
        self._run_helper_case(valid=True, live_parent=True)

    def _run_helper_case(self, valid, locked=False, live_parent=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            install = root / "portable app"
            payload = root / "payload"
            for base in (install, payload):
                (base / "_internal").mkdir(parents=True)
            (install / "ScanBox.exe").write_text("old executable")
            (install / "_internal/old.dll").write_text("old dependency")
            for name in ("config", "engines", "images", "output"):
                (install / name).mkdir()
                (install / name / "keep.txt").write_text("user data")
            if valid:
                (payload / "ScanBox.exe").write_text("new executable")
            (payload / "_internal/new.dll").write_text("new dependency")
            ready = root / "ready"
            log = root / "update.log"
            # Obtain a PID that has actually exited, reproducing the old race.
            parent = subprocess.Popen(["powershell.exe", "-NoProfile", "-Command", "Start-Sleep -Seconds 60" if live_parent else "exit 0"])
            if not live_parent:
                parent.wait(timeout=20)
            lock_handle = None
            kernel = ctypes.WinDLL("kernel32", use_last_error=True)
            kernel.CreateFileW.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p]
            kernel.CreateFileW.restype = ctypes.c_void_p
            kernel.CloseHandle.argtypes = [ctypes.c_void_p]
            if locked:
                lock_handle = kernel.CreateFileW(str(install / "_internal/old.dll"), 0x80000000, 0, None, 3, 0, None)
                self.assertNotIn(lock_handle, (None, ctypes.c_void_p(-1).value))
            command = [
                "powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
                "-File", str(Path(scanbox.__file__).parent / "packaging/portable_updater.ps1"),
                "-ScanBoxProcessId", str(parent.pid), "-PayloadPath", str(payload),
                "-AppDirectory", str(install), "-ReadyPath", str(ready),
                "-LogPath", str(log), "-NoRestart",
            ]
            try:
                helper = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                if live_parent:
                    deadline = time.monotonic() + 15
                    while not ready.exists() and helper.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    self.assertTrue(ready.exists())
                    self.assertIsNone(helper.poll())
                    self.assertEqual((install / "ScanBox.exe").read_text(), "old executable")
                    parent.terminate()
                    parent.wait(timeout=10)
                stdout, stderr = helper.communicate(timeout=30)
            finally:
                if lock_handle:
                    kernel.CloseHandle(lock_handle)
                if parent.poll() is None:
                    parent.terminate()
                    parent.wait(timeout=10)
                if 'helper' in locals() and helper.poll() is None:
                    helper.terminate()
                    helper.wait(timeout=10)
            success = valid and not locked
            self.assertEqual(helper.returncode, 0 if success else 1, stderr)
            self.assertEqual(ready.read_text().strip(), "ready" if valid else "error")
            self.assertEqual((install / "ScanBox.exe").read_text(), "new executable" if success else "old executable")
            for name in ("config", "engines", "images", "output"):
                self.assertEqual((install / name / "keep.txt").read_text(), "user data")
            if success:
                self.assertFalse((install / "_internal/old.dll").exists())
                self.assertTrue((install / "_internal/new.dll").exists())
                self.assertEqual(list(install.glob(".update-backup-*")), [])
                self.assertIn("Update completed", log.read_text(encoding="utf-8-sig"))
            else:
                self.assertIn("Update failed", log.read_text(encoding="utf-8-sig"))
                self.assertTrue((install / "_internal/old.dll").exists())
                if locked:
                    self.assertIn("Previous application restored", log.read_text(encoding="utf-8-sig"))

    def test_prunes_all_update_backups_and_keeps_user_data(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            (install / "ScanBox.exe").write_text("app")
            (install / "_internal").mkdir()
            for name in ("config", "engines", "images", "output"):
                (install / name).mkdir()
                (install / name / "keep.txt").write_text("user data")
            backups = []
            for index, token in enumerate(("aaa", "bbb", "ccc")):
                backup = install / f".update-backup-{token}"
                backup.mkdir()
                (backup / "ScanBox.exe").write_text("previous")
                os.utime(backup, (1000 + index * 100, 1000 + index * 100))
                backups.append(backup)
            staging = install / ".update-new-ddd"
            staging.mkdir()

            with (
                mock.patch.object(scanbox.sys, "frozen", True, create=True),
                mock.patch.object(scanbox.sys, "platform", "win32"),
                mock.patch.object(scanbox, "BASE", str(install)),
            ):
                scanbox._prune_superseded_update_backups()

            self.assertTrue(all(not backup.exists() for backup in backups))
            self.assertFalse(staging.exists())
            self.assertTrue((install / "ScanBox.exe").is_file())
            self.assertTrue((install / "_internal").is_dir())
            for name in ("config", "engines", "images", "output"):
                self.assertEqual((install / name / "keep.txt").read_text(), "user data")

    def test_does_not_prune_from_a_source_checkout(self):
        with tempfile.TemporaryDirectory() as directory:
            install = Path(directory)
            backup = install / ".update-backup-aaa"
            backup.mkdir()

            with (
                mock.patch.object(scanbox.sys, "frozen", False, create=True),
                mock.patch.object(scanbox, "BASE", str(install)),
            ):
                scanbox._prune_superseded_update_backups()

            self.assertTrue(backup.is_dir())

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
