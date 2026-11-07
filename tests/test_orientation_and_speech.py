"""Orientation recovery for misread scans, and paced screen-reader speech."""

import threading
import time
import unittest
from unittest import mock

import scanbox


# Trimmed from real ScanBox logs. UPSIDE_DOWN is a medical referral form fed
# into the scanner the wrong way up; UPRIGHT is the same form rescanned.
UPSIDE_DOWN = """
japeal l.ueal
SJo)1Jew ep!S pue elep waAed
paywat JUOSUOO pue ajnpaD0Jd
weu5aJd
Ileo + 10011 - ueos punoseJ11n
J01Ke1 lenwes
pooeu queeH 01 pues pu oa
epnpxe - Jleo u! VOJWOOS!P ewos suodeJ osp
)SKO euuep - poJ JO eps 0) dwnl wwv
"""

UPRIGHT = """
Request form / Referral
Do not send to My Health Record
Diagnostic Request
Ultrasound scan - L foot + calf
Reason for referral and clinical history
4mm lump to sole of left foot - define ? cyst
also reports some discomfort in left calf - exclude
Follow-up appointment with Referring Doctor:
Patient Identification verified
Procedure and consent verified
"""


class MisreadDetectionTests(unittest.TestCase):
    def test_upside_down_page_is_flagged_as_misread(self):
        self.assertTrue(scanbox.looks_like_misread_document(UPSIDE_DOWN))

    def test_upright_page_is_not_flagged(self):
        self.assertFalse(scanbox.looks_like_misread_document(UPRIGHT))

    def test_upright_page_scores_well_above_the_threshold(self):
        upright, _ = scanbox.document_text_score_per_word(UPRIGHT)
        flipped, _ = scanbox.document_text_score_per_word(UPSIDE_DOWN)
        self.assertGreater(upright, scanbox._MISREAD_SCORE_PER_WORD)
        self.assertLess(flipped, scanbox._MISREAD_SCORE_PER_WORD)
        # The two must not sit near each other, or ordinary pages would start
        # triggering a needless four-way orientation re-read.
        self.assertGreater(upright - flipped, 2.0)

    def test_barely_any_text_is_treated_as_misread(self):
        # A page fed in sideways often yields almost nothing rather than noise.
        self.assertTrue(scanbox.looks_like_misread_document("Ceoccy"))
        self.assertTrue(scanbox.looks_like_misread_document(""))

    def test_scoring_empty_text_reports_no_words(self):
        self.assertEqual(scanbox.document_text_score_per_word(""), (0.0, 0))


class _FakeFrame:
    """Just enough of the frame for _read_upright_if_misread."""

    def __init__(self, upright_text):
        self.upright_text = upright_text
        self.status_messages = []
        self.read_paths = []

    def SetStatusText(self, message):
        self.status_messages.append(message)

    def process_document(self, path, **kwargs):
        self.read_paths.append(path)
        return self.upright_text


class UprightRereadTests(unittest.TestCase):
    def setUp(self):
        patches = [
            mock.patch.object(
                scanbox.wx, "CallAfter", lambda func, *args: func(*args)
            ),
            mock.patch.object(
                scanbox, "rotate_image_clockwise",
                lambda path, degrees: "rotated.png",
            ),
            mock.patch.object(scanbox, "_remove_quietly", lambda *paths: None),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def read_pair(self, frame, text, rotation):
        """Returns (text, rotation applied), as the frame method does."""
        with mock.patch.object(
            scanbox, "detect_document_rotation", return_value=rotation
        ):
            return scanbox.ScanBox._read_upright_if_misread(
                frame, "page.tif", text, 1
            )

    def read(self, frame, text, rotation):
        return self.read_pair(frame, text, rotation)[0]

    def test_a_readable_page_is_returned_without_any_checking(self):
        frame = _FakeFrame(UPRIGHT)
        with mock.patch.object(scanbox, "detect_document_rotation") as detect:
            result, rotation = scanbox.ScanBox._read_upright_if_misread(
                frame, "page.tif", UPRIGHT, 1
            )
        detect.assert_not_called()
        self.assertEqual(result, UPRIGHT)
        self.assertEqual(rotation, 0)
        self.assertEqual(frame.status_messages, [])

    def test_the_applied_rotation_is_reported_back(self):
        # The caller needs it to name the edges of the page the way the
        # reader now has it, not the way it went through the scanner.
        self.assertEqual(
            self.read_pair(_FakeFrame(UPRIGHT), UPSIDE_DOWN, 180)[1], 180
        )
        self.assertEqual(
            self.read_pair(_FakeFrame(UPRIGHT), UPRIGHT, 180)[1], 0
        )
        # A re-read that came out no better left the original text in place,
        # so the original orientation is what the reader has.
        self.assertEqual(
            self.read_pair(_FakeFrame(UPSIDE_DOWN), UPSIDE_DOWN, 180)[1], 0
        )

    def test_upside_down_page_is_re_read_and_the_reason_is_explained(self):
        frame = _FakeFrame(UPRIGHT)
        result = self.read(frame, UPSIDE_DOWN, 180)
        self.assertEqual(frame.read_paths, ["rotated.png"])
        self.assertIn("180 degrees", result)
        self.assertIn("Ultrasound scan", result)
        # The explanation has to be in the text on screen, not only spoken,
        # so it can still be read back after the announcement has gone.
        self.assertTrue(result.startswith("This page was scanned upside down"))

    def test_sideways_page_is_described_as_on_its_side(self):
        result = self.read(_FakeFrame(UPRIGHT), UPSIDE_DOWN, 90)
        self.assertIn("on its side", result)
        self.assertIn("90 degrees", result)

    def test_undetermined_orientation_says_so_and_keeps_the_original(self):
        result = self.read(_FakeFrame(UPRIGHT), UPSIDE_DOWN, None)
        self.assertIn("could not tell", result)
        self.assertIn("japeal", result)

    def test_an_upright_but_unreadable_page_does_not_suggest_turning_it(self):
        result = self.read(_FakeFrame(UPRIGHT), UPSIDE_DOWN, 0)
        self.assertIn("will not help", result)
        self.assertIn("japeal", result)

    def test_a_re_read_that_is_no_better_is_not_passed_off_as_corrected(self):
        frame = _FakeFrame(UPSIDE_DOWN)
        result = self.read(frame, UPSIDE_DOWN, 180)
        self.assertIn("did not come out any better", result)
        self.assertNotIn("corrected reading", result)

    def test_a_failed_re_read_falls_back_to_the_original_text(self):
        frame = _FakeFrame(UPRIGHT)
        frame.process_document = mock.Mock(side_effect=OSError("boom"))
        result = self.read(frame, UPSIDE_DOWN, 180)
        self.assertEqual(result, UPSIDE_DOWN)

    def test_a_failed_orientation_check_falls_back_to_the_original_text(self):
        frame = _FakeFrame(UPRIGHT)
        with mock.patch.object(
            scanbox, "detect_document_rotation",
            side_effect=RuntimeError("boom"),
        ):
            result, rotation = scanbox.ScanBox._read_upright_if_misread(
                frame, "page.tif", UPSIDE_DOWN, 1
            )
        self.assertEqual(result, UPSIDE_DOWN)
        self.assertEqual(rotation, 0)

    def test_a_failure_message_is_never_offered_as_a_corrected_reading(self):
        # "This document could not be read reliably." is fluent English and
        # scores well, so it must not be handed over as a corrected reading.
        frame = _FakeFrame("This document could not be read reliably.")
        result = self.read(frame, UPSIDE_DOWN, 180)
        self.assertNotIn("corrected reading", result)
        self.assertIn("did not come out any better", result)
        self.assertIn("japeal", result)

    def test_the_user_is_told_that_the_extra_check_is_happening(self):
        frame = _FakeFrame(UPRIGHT)
        self.read(frame, UPSIDE_DOWN, 180)
        self.assertTrue(
            any(
                "scanned upright" in message
                for message in frame.status_messages
            ),
            frame.status_messages,
        )


class SpeechPacingTests(unittest.TestCase):
    def setUp(self):
        self.spoken = []
        self.lock = threading.Lock()

        def record(text):
            with self.lock:
                self.spoken.append(text)

        patch = mock.patch.object(scanbox, "_win_speaker", record)
        patch.start()
        self.addCleanup(patch.stop)
        # Pace far faster than real speech so the tests stay quick.
        for name, value in (
            ("_SPEECH_WORDS_PER_MINUTE", 60000),
            ("_SPEECH_MINIMUM_GAP", 0.02),
            ("_SPEECH_MAXIMUM_GAP", 0.05),
        ):
            paced = mock.patch.object(scanbox, name, value)
            paced.start()
            self.addCleanup(paced.stop)

    def drain(self, expected, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                if len(self.spoken) >= expected:
                    return
            time.sleep(0.01)

    def test_a_gap_is_left_for_the_message_to_be_spoken(self):
        self.assertGreaterEqual(
            scanbox._speech_gap_seconds("Scanning."),
            scanbox._SPEECH_MINIMUM_GAP,
        )

    def test_longer_messages_are_given_longer_to_finish(self):
        with mock.patch.object(scanbox, "_SPEECH_MINIMUM_GAP", 0.0), \
             mock.patch.object(scanbox, "_SPEECH_MAXIMUM_GAP", 600.0), \
             mock.patch.object(scanbox, "_SPEECH_WORDS_PER_MINUTE", 300):
            short = scanbox._speech_gap_seconds("Scanning.")
            long_message = scanbox._speech_gap_seconds("word " * 200)
        self.assertGreater(long_message, short)

    def test_the_gap_is_capped_so_the_queue_cannot_stall(self):
        with mock.patch.object(scanbox, "_SPEECH_MAXIMUM_GAP", 1.5):
            self.assertEqual(
                scanbox._speech_gap_seconds("word " * 100000), 1.5
            )

    def test_queued_announcements_are_all_spoken_and_kept_in_order(self):
        scanbox.announce("Scanning with the flatbed.")
        scanbox.announce("Page one is ready.")
        scanbox.announce("Page two is ready.")
        self.drain(3)
        self.assertEqual(self.spoken, [
            "Scanning with the flatbed.",
            "Page one is ready.",
            "Page two is ready.",
        ])

    def test_empty_announcements_are_ignored(self):
        scanbox.announce("")
        scanbox.announce(None)
        time.sleep(0.1)
        self.assertEqual(self.spoken, [])

    def test_live_guidance_drops_stale_queued_positions(self):
        # Replace-pending exists for FaceAlign: a position the user has
        # already moved away from must not be read out late.
        scanbox.announce("Move left.")
        scanbox.announce("Move right.")
        scanbox.announce("Centred.", replace_pending=True)
        self.drain(1)
        time.sleep(0.2)
        self.assertIn("Centred.", self.spoken)
        self.assertNotIn("Move right.", self.spoken)


class UnreadableNoticeTests(unittest.TestCase):
    def test_scanbox_failure_messages_are_recognised_not_scored(self):
        # These read as good English, so they must be matched by name.
        self.assertTrue(scanbox.is_unreadable_notice(
            "This document could not be read reliably."
        ))
        self.assertTrue(scanbox.is_unreadable_notice(
            "The local AI pack is being installed. Scan this page again."
        ))

    def test_a_real_reading_is_not_treated_as_a_failure_message(self):
        self.assertFalse(scanbox.is_unreadable_notice(UPRIGHT))
        self.assertFalse(scanbox.is_unreadable_notice(""))



if __name__ == "__main__":
    unittest.main()
