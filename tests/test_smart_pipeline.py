import unittest

from backend.tools.keyframe_selector import SharpestFrameSelector
from backend.tools.smart_pipeline import (
    SmartPipelineConfig,
    SmartSubtitleScanner,
)
from backend.tools.subtitle_segmenter import (
    FrameAnalysis,
    SubtitleChangeSegmenter,
)


class FakeCapture:
    def __init__(self, values):
        self.frames = [
            [[float(value)] * 4 for _ in range(4)] for value in values
        ]
        self.index = 0

    def isOpened(self):
        return self.index < len(self.frames)

    def read(self):
        if not self.isOpened():
            return False, None
        frame = self.frames[self.index]
        self.index += 1
        return True, frame


class FakeAnalyzer:
    def analyze(self, image):
        value = float(image[0][0])
        return FrameAnalysis(
            signature=[[max(0.0, value)] * 2 for _ in range(2)],
            has_subtitle=value >= 0,
            edge_density=1.0 if value >= 0 else 0.0,
        )


class SubtitleChangeSegmenterTests(unittest.TestCase):
    def test_detects_change_and_blank_without_ocr(self):
        segmenter = SubtitleChangeSegmenter(
            change_threshold=0.25,
            min_segment_frames=1,
            reference_alpha=0.0,
        )
        first = [[0.0] * 4 for _ in range(4)]
        second = [[1.0] * 4 for _ in range(4)]

        self.assertIsNone(
            segmenter.observe(1, first, has_subtitle=True).closed_segment
        )
        self.assertIsNone(
            segmenter.observe(3, first, has_subtitle=True).closed_segment
        )
        changed = segmenter.observe(9, second, has_subtitle=True)
        self.assertEqual(
            (changed.closed_segment.start_frame,
             changed.closed_segment.end_frame),
            (1, 5),
        )
        ended = segmenter.observe(15, second, has_subtitle=False)
        self.assertEqual(
            (ended.closed_segment.start_frame,
             ended.closed_segment.end_frame),
            (6, 11),
        )
        self.assertIsNone(ended.current_segment_id)

    def test_flush_closes_active_segment(self):
        segmenter = SubtitleChangeSegmenter(change_threshold=0.5)
        signature = [[0.0] * 2 for _ in range(2)]
        segmenter.observe(4, signature, has_subtitle=True)
        closed = segmenter.flush(12)
        self.assertEqual((closed.start_frame, closed.end_frame), (4, 12))
        self.assertIsNone(segmenter.flush())


class KeyFrameSelectorTests(unittest.TestCase):
    def test_selects_sharper_frame(self):
        sharp = [
            [255 if (row + column) % 2 else 0 for column in range(128)]
            for row in range(64)
        ]
        blurred = [[127] * 128 for _ in range(64)]
        selector = SharpestFrameSelector()
        selector.consider(10, blurred)
        selector.consider(11, sharp)
        selected = selector.select()
        self.assertEqual(selected.frame_no, 11)
        self.assertGreater(selected.score, 0)


class SmartSubtitleScannerTests(unittest.TestCase):
    def test_returns_one_keyframe_for_each_image_detected_segment(self):
        scanner = SmartSubtitleScanner(
            SmartPipelineConfig(
                scan_fps=30,
                change_threshold=0.5,
                min_edge_density=0,
                min_segment_ms=0,
            ),
            analyzer=FakeAnalyzer(),
        )
        selections = scanner.scan(
            FakeCapture([-1, 0, 0, 1, 1, -1]),
            fps=30,
            total_frames=6,
        )
        self.assertEqual(
            [(item.start_frame, item.end_frame, item.keyframe_no)
             for item in selections],
            [(2, 3, 2), (4, 5, 4)],
        )


if __name__ == "__main__":
    unittest.main()
