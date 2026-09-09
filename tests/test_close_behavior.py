import threading
import unittest
from unittest import mock

import scanbox


class CloseBehaviorTests(unittest.TestCase):
    def test_close_during_processing_cancels_without_busy_dialog(self):
        frame = mock.Mock()
        frame._close_after_announcement = False
        frame.installing = False
        frame.busy = True
        frame.image_export_active = False
        frame._skip_exit_file_cleanup = False
        frame.shutdown_event = threading.Event()
        frame.install_cancel_event = None
        frame.photo_cancel_event = threading.Event()
        frame.batch_cancel_event = threading.Event()
        frame.camera_alignment_stop_event = None
        frame.camera_capture_active = False
        event = mock.Mock()
        event.CanVeto.return_value = True

        with mock.patch.object(scanbox.wx, "MessageBox") as message_box, \
                mock.patch.object(scanbox.wx, "CallLater") as call_later, \
                mock.patch.object(scanbox, "announce"):
            scanbox.ScanBox.on_close(frame, event)

        message_box.assert_not_called()
        self.assertTrue(frame.shutdown_event.is_set())
        self.assertTrue(frame.photo_cancel_event.is_set())
        self.assertTrue(frame.batch_cancel_event.is_set())
        self.assertTrue(frame._skip_exit_file_cleanup)
        event.Veto.assert_called_once_with()
        call_later.assert_called_once_with(500, frame.Close)


if __name__ == "__main__":
    unittest.main()
