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

    def test_prefers_a_card_that_can_hold_the_model_over_a_small_dedicated_one(self):
        """A 2 GB dedicated card is a worse choice than a roomy integrated one."""
        listing = """Available devices:
  Vulkan0: Intel(R) Arc(TM) Graphics (18347 MiB, 17579 MiB free)
  Vulkan1: NVIDIA GeForce MX150 (2048 MiB, 1900 MiB free)
"""
        result = subprocess.CompletedProcess(["runner"], 0, listing, "")
        with mock.patch.object(scanbox.subprocess, "run", return_value=result):
            selected = scanbox._preferred_mtmd_device("runtime/vulkan/runner", 4096)

        self.assertEqual(selected["label"], "Intel(R) Arc(TM) Graphics")

    def test_still_prefers_the_dedicated_card_when_both_fit(self):
        result = subprocess.CompletedProcess(["runner"], 0, DEVICE_LIST, "")
        with mock.patch.object(scanbox.subprocess, "run", return_value=result):
            selected = scanbox._preferred_mtmd_device("runtime/vulkan/runner", 4096)

        self.assertEqual(selected["label"], "NVIDIA GeForce RTX 4070")

    def test_falls_back_to_the_roomiest_card_when_none_can_hold_the_model(self):
        result = subprocess.CompletedProcess(["runner"], 0, DEVICE_LIST, "")
        with mock.patch.object(scanbox.subprocess, "run", return_value=result):
            selected = scanbox._preferred_mtmd_device("runtime/vulkan/runner", 999999)

        self.assertEqual(selected["label"], "Intel(R) Arc(TM) Graphics")

    def test_discrete_intel_arc_outranks_integrated_graphics(self):
        listing = """Available devices:
  Vulkan0: Intel(R) UHD Graphics (8192 MiB, 8000 MiB free)
  Vulkan1: Intel(R) Arc(TM) A770 Graphics (16384 MiB, 6000 MiB free)
"""
        result = subprocess.CompletedProcess(["runner"], 0, listing, "")
        with mock.patch.object(scanbox.subprocess, "run", return_value=result):
            selected = scanbox._preferred_mtmd_device("runtime/vulkan/runner", 4096)

        self.assertEqual(selected["label"], "Intel(R) Arc(TM) A770 Graphics")

    def test_recognises_vulkan_out_of_memory_not_only_metal(self):
        """Windows had no automatic retry because the wording never matched."""
        vulkan = """ggml_vulkan: Device memory allocation of size 2147483648 failed
vk::Device::allocateMemory: ErrorOutOfDeviceMemory
"""

        self.assertTrue(scanbox._mtmd_diagnostics_show_gpu_oom(vulkan))
        self.assertTrue(scanbox._mtmd_diagnostics_show_gpu_oom(
            "kIOGPUCommandBufferCallbackErrorOutOfMemory"
        ))
        self.assertFalse(scanbox._mtmd_diagnostics_show_gpu_oom(
            "error: unknown argument --nope"
        ))

    def test_server_diagnostics_report_exhaustion_and_are_cleaned_up(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "diagnostics.log")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("vk::Device::allocateMemory: ErrorOutOfDeviceMemory" + chr(10))
            with (
                mock.patch.object(scanbox, "_mtmd_server_diagnostics", path),
                mock.patch.object(scanbox, "_mtmd_gpu_out_of_memory", False),
            ):
                scanbox._consume_mtmd_server_diagnostics()

                self.assertTrue(scanbox._mtmd_gpu_out_of_memory)
                self.assertIsNone(scanbox._mtmd_server_diagnostics)
            self.assertFalse(os.path.exists(path))

    def test_model_memory_requirement_covers_the_weights(self):
        import tempfile, os
        with tempfile.TemporaryDirectory() as directory:
            files = []
            for name in ("model.gguf", "mmproj.gguf"):
                path = os.path.join(directory, name)
                with open(path, "wb") as handle:
                    handle.write(bytes(1024 * 1024))
                files.append(path)

            required = scanbox._mtmd_model_memory_requirement_mib(files)

        self.assertGreater(required, 2)

    def test_report_states_the_card_the_run_actually_used(self):
        """The report must describe what happened, not re-run the choice."""
        with (
            mock.patch.object(scanbox, "_find_mtmd_model_files", return_value=["m", "p"]),
            mock.patch.object(scanbox, "_find_mtmd_runners", return_value=["vulkan/runner"]),
            mock.patch.object(scanbox, "external_ai_config", return_value=None),
            mock.patch.object(scanbox, "_mtmd_active_backend", None),
            mock.patch.object(scanbox, "_mtmd_last_backend", "GPU via Vulkan"),
            mock.patch.object(scanbox, "_mtmd_active_device", None),
            mock.patch.object(scanbox, "_mtmd_last_device", "NVIDIA GeForce RTX 4070"),
            mock.patch.object(scanbox, "_preferred_mtmd_device") as picker,
        ):
            report = scanbox.local_ai_acceleration_report("qwen3_vl_2b")

        self.assertIn("NVIDIA GeForce RTX 4070", report)
        picker.assert_not_called()

    def test_report_waits_for_a_real_run_before_naming_a_card(self):
        with (
            mock.patch.object(scanbox, "_find_mtmd_model_files", return_value=["m", "p"]),
            mock.patch.object(scanbox, "_find_mtmd_runners", return_value=["vulkan/runner"]),
            mock.patch.object(scanbox, "external_ai_config", return_value=None),
            mock.patch.object(scanbox, "_mtmd_active_backend", None),
            mock.patch.object(scanbox, "_mtmd_last_backend", None),
        ):
            report = scanbox.local_ai_acceleration_report("qwen3_vl_2b")

        self.assertIn("describe an image first", report)

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
