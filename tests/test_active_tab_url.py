import unittest

from get_active_tab_url import ActiveTabUrlError, normalize_web_url


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


if __name__ == "__main__":
    unittest.main()
