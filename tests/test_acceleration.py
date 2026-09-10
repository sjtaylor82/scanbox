import io
import json
import subprocess
import unittest
from types import SimpleNamespace
from unittest import mock

import scanbox


DEVICE_LIST = """Available devices:
  Vulkan0: Intel(R) Arc(TM) Graphics (18347 MiB, 17579 MiB free)
  Vulkan1: NVIDIA GeForce RTX 4070 (12282 MiB, 11000 MiB free)
"""


class AccelerationTests(unittest.TestCase):
    def test_runtime_download_is_pinned_to_tested_release(self):
        self.assertTrue(scanbox.MTMD_RUNTIME_RELEASE_URL.endswith("/tags/b10216"))

    def test_parses_vulkan_devices(self):
        devices = scanbox._parse_mtmd_devices(DEVICE_LIST)

        self.assertEqual(devices[0]["id"], "Vulkan0")
        self.assertEqual(devices[1]["label"], "NVIDIA GeForce RTX 4070")
        self.assertEqual(devices[1]["free_mib"], 11000)

    def test_prefers_dedicated_gpu_over_integrated_gpu(self):
        result = subprocess.CompletedProcess(
            ["runner", "--list-devices"], 0, DEVICE_LIST, ""
        )
        with mock.patch.object(scanbox.subprocess, "run", return_value=result):
            selected = scanbox._preferred_mtmd_device("runtime/vulkan/runner")

        self.assertEqual(selected["id"], "Vulkan1")
        self.assertEqual(selected["label"], "NVIDIA GeForce RTX 4070")

    def test_does_not_select_vulkan_device_for_other_runners(self):
        with mock.patch.object(scanbox.subprocess, "run") as run:
            selected = scanbox._preferred_mtmd_device("runtime/cpu/runner")

        self.assertIsNone(selected)
        run.assert_not_called()

    def test_windows_cpu_only_runtime_needs_repair(self):
        with mock.patch.object(
            scanbox,
            "_find_mtmd_runners",
            return_value=[r"runtime\cpu\llama-mtmd-cli.exe"],
        ):
            self.assertTrue(scanbox._mtmd_runtime_needs_repair("win32"))

    def test_windows_vulkan_runtime_does_not_need_repair(self):
        with mock.patch.object(
            scanbox,
            "_find_mtmd_runners",
            return_value=[r"runtime\vulkan\llama-mtmd-cli.exe"],
        ):
            self.assertFalse(scanbox._mtmd_runtime_needs_repair("win32"))

    def test_missing_macos_runtime_needs_repair(self):
        with mock.patch.object(scanbox, "_find_mtmd_runners", return_value=[]):
            self.assertTrue(scanbox._mtmd_runtime_needs_repair("darwin"))

    def test_cpu_only_install_rejects_release_without_vulkan_asset(self):
        release = io.BytesIO(json.dumps({"assets": []}).encode("utf-8"))
        with (
            mock.patch.object(scanbox.sys, "platform", "win32"),
            mock.patch.object(
                scanbox,
                "_find_mtmd_runners",
                return_value=[r"runtime\cpu\llama-mtmd-cli.exe"],
            ),
            mock.patch.object(scanbox.urllib.request, "urlopen", return_value=release),
        ):
            result = scanbox._install_mtmd_runtime()

        self.assertIn("vulkan", result)
        self.assertIn("could not be found", result)

    def test_preload_schedules_automatic_runtime_repair(self):
        frame = SimpleNamespace(
            app_settings={"vision_model": "qwen3_vl_2b"},
            start_install=mock.Mock(),
        )
        with (
            mock.patch.object(scanbox, "_find_mtmd_model_files", return_value=object()),
            mock.patch.object(scanbox, "_mtmd_runtime_needs_repair", return_value=True),
            mock.patch.object(scanbox.wx, "CallAfter") as call_after,
        ):
            scanbox.ScanBox._preload_local_ai(frame)

        call_after.assert_called_once_with(frame.start_install, "qwen3_vl_2b")


if __name__ == "__main__":
    unittest.main()
