import sys
import types
import unittest
from unittest import mock

from get_active_tab_url import (
    ActiveTabUrlError,
    is_session_bound_download_url,
    make_browser_download_request,
    normalize_web_url,
    open_with_browser_session,
    warm_up_active_tab_url_reader,
)


class NormalizeWebUrlTests(unittest.TestCase):
    def test_accepts_https_and_removes_fragment(self):
        self.assertEqual(
            normalize_web_url("https://example.com/menu.pdf#page=2"),
            "https://example.com/menu.pdf",
        )

    def test_adds_https_to_browser_display_value(self):
        self.assertEqual(
            normalize_web_url("example.com/menu.pdf"),
            "https://example.com/menu.pdf",
        )

    def test_rejects_browser_internal_url(self):
        with self.assertRaises(ActiveTabUrlError):
            normalize_web_url("chrome://settings")

    def test_rejects_non_url_text(self):
        with self.assertRaises(ActiveTabUrlError):
            normalize_web_url("not a URL")


class BrowserDownloadRequestTests(unittest.TestCase):
    def test_windows_warmup_initializes_com_on_worker_thread(self):
        events = []
        pythoncom = types.ModuleType("pythoncom")
        pythoncom.CoInitialize = lambda: events.append("com")
        pywinauto = types.ModuleType("pywinauto")
        pywinauto.Desktop = lambda **kwargs: events.append(
            ("desktop", kwargs["backend"])
        )

        with mock.patch("get_active_tab_url.sys.platform", "win32"), \
                mock.patch.dict(
                    sys.modules,
                    {"pythoncom": pythoncom, "pywinauto": pywinauto},
                ):
            warm_up_active_tab_url_reader()

        self.assertEqual(events, ["com", ("desktop", "uia")])

    def test_uses_browser_headers_for_temporary_content_links(self):
        url = "https://example.dl.dropboxusercontent.com/cd/0/inline2/token/file"
        request = make_browser_download_request(url)

        self.assertEqual(request.full_url, url)
        self.assertIn("Mozilla/5.0", request.get_header("User-agent"))
        self.assertIn("application/pdf", request.get_header("Accept"))

    def test_recognizes_dropbox_browser_session_delivery_url(self):
        self.assertTrue(is_session_bound_download_url(
            "https://ucf123.dl.dropboxusercontent.com/cd/0/inline2/token/file"
        ))
        self.assertFalse(is_session_bound_download_url(
            "https://dl.dropboxusercontent.com/s/public/document.pdf"
        ))
        self.assertFalse(is_session_bound_download_url(
            "https://example.com/cd/0/inline2/token/file"
        ))

    def test_browser_session_download_uses_scoped_cookies(self):
        fake_jar = object()
        fake_response = object()
        fake_loader = mock.Mock(return_value=fake_jar)
        fake_opener = mock.Mock()
        fake_opener.open.return_value = fake_response
        fake_module = mock.Mock(chrome=fake_loader)
        fake_module.edge = fake_module.firefox = fake_module.safari = None

        with mock.patch("get_active_tab_url.browser_cookie3", fake_module), \
                mock.patch("urllib.request.build_opener", return_value=fake_opener):
            result = open_with_browser_session(
                "https://ucf123.dl.dropboxusercontent.com/cd/0/inline2/token/file"
            )

        self.assertIs(result, fake_response)
        fake_loader.assert_called_once_with(domain_name="dropboxusercontent.com")
        self.assertEqual(fake_opener.open.call_args.kwargs["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
