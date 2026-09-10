import io
import ast
import base64
from pathlib import Path
import json
import os
import tempfile
import threading
import unittest
from types import SimpleNamespace
from unittest import mock

import numpy as np

import scanbox


class JsonResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.close()


class LocalAiDiscoveryTests(unittest.TestCase):
    def test_ollama_probe_returns_every_model(self):
        service = {
            "kind": "ollama",
            "name": "Ollama",
            "models_url": "http://127.0.0.1:11434/api/tags",
            "base_url": "http://127.0.0.1:11434",
        }
        response = JsonResponse(json.dumps({
            "models": [
                {"name": "deepseek-ocr:latest"},
                {"name": "qwen3-vl:2b"},
            ]
        }).encode("utf-8"))
        with mock.patch.object(
            scanbox.urllib.request, "urlopen", return_value=response
        ):
            found = scanbox._probe_local_ai_service(service)

        self.assertEqual(
            [item["model"] for item in found],
            ["deepseek-ocr:latest", "qwen3-vl:2b"],
        )

    def test_discovery_keeps_models_from_multiple_services(self):
        def probe(service, timeout):
            return [{
                "kind": service["kind"],
                "name": service["name"],
                "url": service["base_url"],
                "model": service["name"].lower(),
                "label": service["name"],
            }]

        with mock.patch.object(scanbox, "_probe_local_ai_service", side_effect=probe):
            found = scanbox.discover_local_ai_models()

        self.assertEqual(len(found), len(scanbox.LOCAL_AI_SERVICES))
        self.assertEqual(
            [item["name"] for item in found],
            [service["name"] for service in scanbox.LOCAL_AI_SERVICES],
        )

    def test_external_provider_must_be_on_this_computer(self):
        remote = {
            "ai_provider": "external",
            "external_ai_kind": "openai",
            "external_ai_url": "https://example.com/v1",
            "external_ai_model": "vision-model",
            "external_ai_label": "Remote",
        }
        self.assertIsNone(scanbox.external_ai_config(remote))

    def test_openai_compatible_image_request(self):
        settings = {
            "ai_provider": "external",
            "external_ai_kind": "openai",
            "external_ai_url": "http://127.0.0.1:8000/v1",
            "external_ai_model": "deepseek-ocr",
            "external_ai_label": "vLLM",
        }
        captured = {}

        def urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            return JsonResponse(json.dumps({
                "choices": [{"message": {"content": "Recognised text"}}]
            }).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "page.png")
            scanbox.Image.new("RGB", (10, 10), "white").save(image_path)
            with mock.patch.object(scanbox.urllib.request, "urlopen", side_effect=urlopen):
                result = scanbox.run_external_ai_task(
                    "transcribe", image_path, settings=settings
                )

        self.assertEqual(result, "Recognised text")
        self.assertEqual(captured["url"], "http://127.0.0.1:8000/v1/chat/completions")
        content = captured["payload"]["messages"][0]["content"]
        self.assertEqual(content[1]["type"], "image_url")

    def test_ollama_image_request_uses_native_api(self):
        settings = {
            "ai_provider": "external",
            "external_ai_kind": "ollama",
            "external_ai_url": "http://127.0.0.1:11434",
            "external_ai_model": "qwen3-vl:2b",
            "external_ai_label": "Ollama",
        }
        captured = {}

        def urlopen(request, timeout):
            captured["url"] = request.full_url
            captured["payload"] = json.loads(request.data)
            return JsonResponse(json.dumps({
                "message": {"content": "A detailed description"}
            }).encode("utf-8"))

        with tempfile.TemporaryDirectory() as directory:
            image_path = os.path.join(directory, "photo.jpg")
            scanbox.Image.new("RGB", (10, 10), "white").save(image_path)
            with mock.patch.object(scanbox.urllib.request, "urlopen", side_effect=urlopen):
                result = scanbox.run_external_ai_task(
                    "describe", image_path, settings=settings
                )

        self.assertEqual(result, "A detailed description")
        self.assertEqual(captured["url"], "http://127.0.0.1:11434/api/chat")
        encoded = captured["payload"]["messages"][0]["images"][0]
        with scanbox.Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
            self.assertEqual(image.format, "PNG")


class ExternalAiRegressionTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "ai_provider": "external", "external_ai_kind": "openai",
            "external_ai_url": "http://127.0.0.1:1234/v1",
            "external_ai_model": "vision-model",
        }

    def test_tiff_is_converted_without_changing_source_for_both_providers(self):
        for kind in ("openai", "ollama"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "scan.tif"
                scanbox.Image.new("RGB", (20, 30), "red").save(path)
                original = path.read_bytes()
                response = {"choices": [{"message": {"content": "Complete page"}}],
                            "message": {"content": "Complete page"}}
                with mock.patch.object(scanbox.urllib.request, "urlopen", return_value=JsonResponse(json.dumps(response).encode())) as request:
                    scanbox.run_external_ai_task("transcribe", path, settings={**self.settings, "external_ai_kind": kind})
                payload = json.loads(request.call_args.args[0].data)
                if kind == "openai":
                    url = payload["messages"][0]["content"][1]["image_url"]["url"]
                    self.assertTrue(url.startswith("data:image/png;base64,"))
                    encoded = url.split(",", 1)[1]
                    self.assertEqual(payload["max_tokens"], 8192)
                else:
                    encoded = payload["messages"][0]["images"][0]
                    self.assertEqual(payload["options"]["num_predict"], 8192)
                with scanbox.Image.open(io.BytesIO(base64.b64decode(encoded))) as image:
                    self.assertEqual(image.format, "PNG")
                    self.assertEqual(image.size, (20, 30))
                    self.assertEqual(image.getpixel((0, 0)), (255, 0, 0))
                self.assertEqual(path.read_bytes(), original)

    def test_truncated_document_keeps_returned_text_for_both_providers(self):
        for kind in ("openai", "ollama"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "page.png"
                scanbox.Image.new("RGB", (10, 10)).save(path)
                settings = {**self.settings, "external_ai_kind": kind}
                response = {"choices": [{"finish_reason": "length", "message": {"content": "Partial page"}}],
                            "done_reason": "length", "message": {"content": "Partial page"}}
                with (
                    mock.patch.object(scanbox, "read_app_settings", return_value=settings),
                    mock.patch.object(scanbox.sys, "platform", "win32"),
                    mock.patch.object(scanbox.urllib.request, "urlopen", return_value=JsonResponse(json.dumps(response).encode())),
                    mock.patch.object(scanbox, "windows_ocr", return_value="Complete native page") as native,
                ):
                    text = scanbox.ScanBox.process_document(SimpleNamespace(app_settings=settings), path, detect_page=False)
                self.assertTrue(text.startswith("Partial page"))
                self.assertIn("output token limit", text)
                native.assert_not_called()

    def test_custom_port_discovery_and_api_key(self):
        responses = [
            JsonResponse(json.dumps({"data": [{"id": "deepseek-ocr"}]}).encode()),
            JsonResponse(json.dumps({"models": []}).encode()),
        ]
        with mock.patch.object(
            scanbox.urllib.request, "urlopen", side_effect=responses
        ) as urlopen:
            found = scanbox.discover_local_ai_models_at(
                "http://127.0.0.1:8001", "secret"
            )
        requests = [call.args[0] for call in urlopen.call_args_list]
        self.assertEqual(
            {request.full_url for request in requests},
            {"http://127.0.0.1:8001/v1/models", "http://127.0.0.1:8001/api/tags"},
        )
        self.assertTrue(all(request.get_header("Authorization") == "Bearer secret"
                            for request in requests))
        self.assertEqual(found[0]["model"], "deepseek-ocr")

    def test_screen_ocr_prefers_selected_provider_and_falls_back_on_failure(self):
        for answer, expected in (("A complete external screen transcription", "A complete external screen transcription"),
                                 ("Local AI service returned incomplete text", "One two three four five six")):
            with self.subTest(answer=answer), mock.patch.object(scanbox.sys, "platform", "win32"), mock.patch.object(scanbox, "run_external_ai_task", return_value=answer) as external, mock.patch.object(scanbox, "windows_ocr", return_value="One two three four five six") as native:
                text = scanbox.ScanBox._read_screen_text(SimpleNamespace(app_settings=self.settings), "screen.png", use_vision_fallback=True)
                self.assertEqual(text, expected)
                external.assert_called_once()
                self.assertEqual(native.called, answer.startswith("Local AI service"))

    def test_cancelled_screen_ocr_does_not_start_native_fallback(self):
        cancel = threading.Event()
        for already_cancelled in (False, True):
            with self.subTest(already_cancelled=already_cancelled):
                if already_cancelled:
                    cancel.set()
                with mock.patch.object(scanbox, "run_external_ai_task", return_value=scanbox.PHOTO_DESCRIPTION_CANCELLED) as external, mock.patch.object(scanbox, "windows_ocr") as native:
                    text = scanbox.ScanBox._read_screen_text(SimpleNamespace(app_settings=self.settings), "screen.png", use_vision_fallback=True, cancel_event=cancel)
                    self.assertEqual(text, scanbox.PHOTO_DESCRIPTION_CANCELLED)
                    native.assert_not_called()
                    self.assertEqual(external.called, not already_cancelled)


class ScannerSelectionTests(unittest.TestCase):
    def test_discovery_preserves_ask_each_time_and_explicit_selection(self):
        # Exercise the actual nested callback without opening the settings GUI.
        tree = ast.parse(Path(scanbox.__file__).read_text(encoding="utf-8"))
        callback = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "finish_scanner_discovery")
        callback.body = [n for n in callback.body if not isinstance(n, ast.Nonlocal)]
        callback.body.insert(0, ast.Global(names=["scanner_options"]))
        code = compile(ast.fix_missing_locations(ast.Module(body=[callback], type_ignores=[])), "scanner_callback", "exec")
        for selection in (0, 1):
            for connected in (False, True):
                with self.subTest(selection=selection, connected=connected):
                    scanner = {"id": "saved", "name": "Desk scanner"}
                    choice = mock.Mock()
                    choice.GetSelection.return_value = selection
                    namespace = {"scanner_options": [scanner], "saved_scanner_id": "saved",
                                 "saved_scanner_name": "Desk scanner", "scanner_choice": choice,
                                 "scanner_status": mock.Mock(), "refresh_scanners_btn": mock.Mock(),
                                 "scanner_panel": mock.Mock()}
                    exec(code, namespace)
                    namespace["finish_scanner_discovery"]([scanner] if connected else [])
                    choice.SetSelection.assert_called_once_with(selection)

    def test_windows_scanner_discovery_deduplicates_device_id(self):
        scanner = SimpleNamespace(
            Type=1,
            DeviceID="scanner-1",
            Properties=lambda name: SimpleNamespace(Value="Office scanner"),
        )

        class DeviceInfos:
            Count = 2

            def __getitem__(self, index):
                return scanner

        manager = SimpleNamespace(DeviceInfos=DeviceInfos())
        client = SimpleNamespace(Dispatch=mock.Mock(return_value=manager))
        frame = SimpleNamespace(_wia_scanner_name=scanbox.ScanBox._wia_scanner_name)
        with (
            mock.patch.object(scanbox.sys, "platform", "win32"),
            mock.patch.object(scanbox, "win32com", SimpleNamespace(client=client)),
        ):
            found = scanbox.ScanBox.available_scanners(frame)

        self.assertEqual(found, [{"id": "scanner-1", "name": "Office scanner"}])

    def test_saved_scanner_is_used_without_prompting(self):
        frame = SimpleNamespace(app_settings={"scanner_id": "scanner-2"})
        scanners = [
            {"id": "scanner-1", "name": "Office scanner"},
            {"id": "scanner-2", "name": "Desk scanner"},
        ]
        with mock.patch.object(scanbox.wx, "SingleChoiceDialog") as dialog:
            selected = scanbox.ScanBox.selected_scanner(frame, scanners)

        self.assertEqual(selected, scanners[1])
        dialog.assert_not_called()

    def test_missing_saved_scanner_does_not_choose_another(self):
        frame = SimpleNamespace(app_settings={"scanner_id": "disconnected"})
        scanners = [{"id": "scanner-1", "name": "Office scanner"}]

        selected = scanbox.ScanBox.selected_scanner(frame, scanners)

        self.assertIsNone(selected)


class CameraCaptureTests(unittest.TestCase):
    def test_saved_camera_is_used_without_prompting(self):
        frame = SimpleNamespace(app_settings={"camera_index": 2})
        frame.available_camera_indexes = mock.Mock(return_value=[0, 2])
        frame.camera_device_names = mock.Mock(return_value={0: "Webcam", 2: "Document camera"})
        with mock.patch.object(scanbox.wx, "Dialog") as dialog:
            selected = scanbox.ScanBox.choose_camera(frame)
        self.assertEqual(selected, 2)
        dialog.assert_not_called()

    def test_missing_saved_camera_does_not_choose_another(self):
        frame = SimpleNamespace(app_settings={"camera_index": 2, "camera_name": "Document camera"})
        frame.available_camera_indexes = mock.Mock(return_value=[0])
        frame.camera_device_names = mock.Mock(return_value={0: "Webcam"})
        with mock.patch.object(scanbox.wx, "MessageBox") as message:
            selected = scanbox.ScanBox.choose_camera(frame)
        self.assertIsNone(selected)
        message.assert_called_once()

    def test_saved_camera_is_relocated_by_name_after_reordering(self):
        frame = SimpleNamespace(app_settings={"camera_index": 2, "camera_name": "Document camera"})
        frame.available_camera_indexes = mock.Mock(return_value=[0, 1])
        frame.camera_device_names = mock.Mock(
            return_value={0: "Document camera", 1: "Webcam"}
        )
        self.assertEqual(scanbox.ScanBox.choose_camera(frame), 0)

    def test_blank_bridge_frames_are_rejected(self):
        blank = np.zeros((10, 10, 3), dtype=np.uint8)
        white = np.full((10, 10, 3), 255, dtype=np.uint8)

        self.assertFalse(scanbox._camera_frame_is_usable(blank))
        self.assertFalse(scanbox._camera_frame_is_usable(white))

    def test_reader_waits_for_usable_bridge_frame(self):
        blank = np.zeros((10, 10, 3), dtype=np.uint8)
        page = blank.copy()
        page[2:8, 2:8] = 180
        camera = mock.Mock()
        camera.read.side_effect = [(True, blank), (True, page)]

        frame, attempts = scanbox._read_settled_camera_frame(
            camera,
            settle_seconds=0,
            timeout_seconds=1,
            read_interval=0,
        )

        self.assertIs(frame, page)
        self.assertEqual(attempts, 2)

    def test_camera_workflow_releases_retained_device(self):
        camera = mock.Mock()
        frame = SimpleNamespace(
            camera_capture_timer=mock.Mock(),
            camera_capture_device=camera,
            camera_capture_active=True,
            camera_capture_stopping=True,
            camera_capture_index=2,
            update_controls=mock.Mock(),
        )
        frame.close_camera_capture_device = lambda: (
            scanbox.ScanBox.close_camera_capture_device(frame)
        )

        scanbox.ScanBox.complete_camera_workflow(frame)

        camera.release.assert_called_once_with()
        self.assertIsNone(frame.camera_capture_device)


if __name__ == "__main__":
    unittest.main()
