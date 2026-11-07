"""Noticing when part of a page fell outside the scan."""

import unittest
from unittest import mock

import scanbox


def ocr_result(width, height, words):
    """Shape a windows_ocr_word_boxes return value."""
    return width, height, words


def word(text, left, top, width=60, height=20):
    return (text, left, top, width, height)


# A page 1000 wide by 1400 tall. The tolerance is 0.4%, so 4px horizontally
# and about 5px vertically count as touching the border.
PAGE = (1000, 1400)


class DetectClippedEdgesTests(unittest.TestCase):
    def detect(self, words, size=PAGE, rotation=0):
        measured = ocr_result(size[0], size[1], words)
        with mock.patch.object(
            scanbox, "windows_ocr_word_boxes", return_value=measured
        ) as boxes:
            found = scanbox.detect_clipped_edges("page.tif", rotation)
        return found, boxes

    def test_a_page_with_clear_margins_is_not_flagged(self):
        found, _ = self.detect([
            word("Referral", 120, 200),
            word("Ultrasound", 120, 400),
            word("Capalaba", 120, 600),
        ])
        self.assertEqual(found, [])

    def test_words_running_off_the_right_are_reported(self):
        found, _ = self.detect([
            word("Referral", 120, 200),
            word("book-onlil", 940, 300),
            word("verified", 945, 700),
            word("m", 980, 740, width=20),
        ])
        self.assertEqual(found, ["right"])

    def test_several_cut_edges_are_all_reported_in_a_fixed_order(self):
        found, _ = self.detect([
            word("cut", 0, 300),
            word("off", 1, 400),
            word("lost", 950, 300),
            word("gone", 960, 400),
        ])
        self.assertEqual(found, ["left", "right"])

    def test_one_word_against_the_margin_is_not_enough(self):
        # Plenty of legitimate layouts put a single word hard against an
        # edge. Warning on that would teach the user to ignore the warning.
        found, _ = self.detect([
            word("Referral", 120, 200),
            word("edge", 950, 300),
        ])
        self.assertEqual(found, [])

    def test_a_full_bleed_header_bar_does_not_count_as_clipping(self):
        # The bar reaches the paper edge by design and would defeat any
        # ink-at-the-border test. No words are recognised inside it, so
        # asking the OCR engine where the words are ignores it for free.
        found, _ = self.detect([
            word("Request", 120, 200),
            word("Referral", 300, 200),
        ])
        self.assertEqual(found, [])

    def test_the_top_and_bottom_edges_are_checked_too(self):
        found, _ = self.detect([
            word("header", 200, 0),
            word("cut", 400, 2),
            word("footer", 200, 1385),
            word("cut", 400, 1388),
        ])
        self.assertEqual(found, ["top", "bottom"])

    def test_blank_words_are_ignored(self):
        found, _ = self.detect([
            word("", 0, 300),
            word("   ", 0, 400),
        ])
        self.assertEqual(found, [])

    def test_a_rotated_page_is_measured_after_being_turned_upright(self):
        # The edges must be named as the reader now has them, not as the
        # page happened to go through the scanner.
        with mock.patch.object(
            scanbox, "rotate_image_clockwise", return_value="upright.png"
        ) as rotate, mock.patch.object(
            scanbox, "_remove_quietly"
        ) as remove, mock.patch.object(
            scanbox, "windows_ocr_word_boxes",
            return_value=ocr_result(1000, 1400, [word("a", 950, 10),
                                                 word("b", 960, 50)]),
        ) as boxes:
            found = scanbox.detect_clipped_edges("page.tif", 180)
        rotate.assert_called_once_with("page.tif", 180)
        boxes.assert_called_once_with("upright.png")
        remove.assert_called_once_with("upright.png")
        self.assertEqual(found, ["right"])

    def test_an_unrotated_page_is_measured_directly(self):
        found, boxes = self.detect([word("a", 120, 200)])
        boxes.assert_called_once_with("page.tif")

    def test_no_word_boxes_available_means_no_claim_either_way(self):
        with mock.patch.object(
            scanbox, "windows_ocr_word_boxes", return_value=None
        ):
            self.assertEqual(scanbox.detect_clipped_edges("page.tif"), [])

    def test_an_empty_page_is_not_flagged(self):
        found, _ = self.detect([])
        self.assertEqual(found, [])

    def test_a_failure_is_swallowed_rather_than_breaking_the_scan(self):
        with mock.patch.object(
            scanbox, "windows_ocr_word_boxes", side_effect=OSError("boom")
        ):
            self.assertEqual(scanbox.detect_clipped_edges("page.tif"), [])


class DescribeEdgeListTests(unittest.TestCase):
    def test_wording(self):
        self.assertEqual(scanbox.describe_edge_list([]), "")
        self.assertEqual(
            scanbox.describe_edge_list(["right"]), "the right edge"
        )
        self.assertEqual(
            scanbox.describe_edge_list(["top", "right"]),
            "the top and right edges",
        )
        self.assertEqual(
            scanbox.describe_edge_list(["left", "top", "right"]),
            "the left, top and right edges",
        )


class _FakeFrame:
    pass


class ClippedEdgeWarningTests(unittest.TestCase):
    def warn(self, edges):
        with mock.patch.object(
            scanbox, "detect_clipped_edges", return_value=edges
        ):
            return scanbox.ScanBox._warn_about_clipped_edges(
                _FakeFrame(), "page.tif", 0, 1
            )

    def test_nothing_is_said_about_a_fully_captured_page(self):
        self.assertEqual(self.warn([]), "")

    def test_the_notice_names_the_edge_and_says_what_to_do(self):
        notice = self.warn(["right"])
        self.assertIn("cut off at the right edge", notice)
        self.assertIn("words are missing", notice)
        self.assertIn("scan it again", notice)
        # It goes into the text on screen, ahead of the reading itself, so
        # it survives the announcement being interrupted.
        self.assertTrue(notice.endswith("\n\n"))

    def test_the_rotation_is_passed_through_to_the_check(self):
        with mock.patch.object(
            scanbox, "detect_clipped_edges", return_value=[]
        ) as detect:
            scanbox.ScanBox._warn_about_clipped_edges(
                _FakeFrame(), "page.tif", 180, 1
            )
        detect.assert_called_once_with("page.tif", 180)

    def test_a_failure_leaves_the_reading_alone(self):
        with mock.patch.object(
            scanbox, "detect_clipped_edges", side_effect=RuntimeError("boom")
        ):
            notice = scanbox.ScanBox._warn_about_clipped_edges(
                _FakeFrame(), "page.tif", 0, 1
            )
        self.assertEqual(notice, "")


if __name__ == "__main__":
    unittest.main()
