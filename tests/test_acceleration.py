import subprocess
import unittest
from unittest import mock

import scanbox


DEVICE_LIST = """Available devices:
  Vulkan0: Intel(R) Arc(TM) Graphics (18347 MiB, 17579 MiB free)
  Vulkan1: NVIDIA GeForce RTX 4070 (12282 MiB, 11000 MiB free)
"""


class AccelerationTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
