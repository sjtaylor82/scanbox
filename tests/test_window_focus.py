"""Bringing a native window to the foreground, safely."""

import sys
import unittest
from unittest import mock

import scanbox


class BringWindowToFrontTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_an_integer_handle_is_passed_straight_through(self):
        user32 = mock.Mock()
        with mock.patch.object(scanbox.ctypes, "windll", mock.Mock(user32=user32)):
            scanbox.bring_window_to_front(12345)
        user32.SetForegroundWindow.assert_called_once_with(12345)

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_a_handle_that_is_not_a_number_is_refused_not_chased(self):
        # A non-integer handle used to reach ctypes directly, which then
        # followed _as_parameter_ on it. On an object that answers every
        # attribute lookup with another such object - a test double, say -
        # that chase never ends, and the C stack overflow killed the whole
        # process instead of raising something catchable.
        user32 = mock.Mock()
        with mock.patch.object(scanbox.ctypes, "windll", mock.Mock(user32=user32)):
            scanbox.bring_window_to_front(mock.Mock())
        user32.SetForegroundWindow.assert_not_called()

    @unittest.skipUnless(sys.platform == "win32", "Windows only")
    def test_a_failure_from_windows_is_swallowed(self):
        user32 = mock.Mock()
        user32.SetForegroundWindow.side_effect = OSError("no such window")
        with mock.patch.object(scanbox.ctypes, "windll", mock.Mock(user32=user32)):
            scanbox.bring_window_to_front(12345)

    @unittest.skipIf(sys.platform == "win32", "non-Windows only")
    def test_nothing_is_attempted_away_from_windows(self):
        scanbox.bring_window_to_front(12345)


if __name__ == "__main__":
    unittest.main()
