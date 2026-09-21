"""Image-only subtitle change detection.

This module deliberately knows nothing about OCR.  It turns sampled subtitle
ROI images into time segments; recognition can then run only on the best frame
from each segment.
"""

from dataclasses import dataclass
from copy import deepcopy
from typing import Any

try:
    import numpy as np
except ImportError:  # The state machine itself is dependency-free.
    np = None

try:
    import cv2
except ImportError:  # Pure state-machine tests do not require OpenCV.
    cv2 = None


@dataclass(frozen=True)
class FrameAnalysis:
    signature: Any
    has_subtitle: bool
    edge_density: float


@dataclass(frozen=True)
class SubtitleSegment:
    segment_id: int
    start_frame: int
    end_frame: int


@dataclass(frozen=True)
class SegmentationDecision:
    current_segment_id: int | None
    closed_segment: SubtitleSegment | None = None
    change_score: float = 0.0


class SubtitleFrameAnalyzer:
    """Build a compact edge signature and estimate whether text is present."""

    def __init__(self, signature_width=320, min_edge_density=0.004):
        self.signature_width = max(32, int(signature_width))
        self.min_edge_density = max(0.0, float(min_edge_density))

    def analyze(self, image):
        if cv2 is None or np is None:
            raise RuntimeError(
                "OpenCV and NumPy are required for subtitle frame analysis"
            )
        if image is None or image.size == 0:
            return FrameAnalysis(
                np.zeros((1, self.signature_width), dtype=np.float32),
                False,
                0.0,
            )
        if image.ndim == 3:
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        else:
            gray = image
        height, width = gray.shape[:2]
        target_height = max(16, round(height * self.signature_width / width))
        resized = cv2.resize(
            gray,
            (self.signature_width, target_height),
            interpolation=cv2.INTER_AREA,
        )
        blurred = cv2.GaussianBlur(resized, (3, 3), 0)
        edges = cv2.Canny(blurred, 60, 180)
        # Closing reconnects thin antialiased glyph strokes and makes the
        # signature less sensitive to compression noise.
        edges = cv2.morphologyEx(
            edges, cv2.MORPH_CLOSE, np.ones((2, 2), dtype=np.uint8)
        )
        signature = edges.astype(np.float32) / 255.0
        density = float(np.mean(signature))
        return FrameAnalysis(
            signature=signature,
            has_subtitle=density >= self.min_edge_density,
            edge_density=density,
        )


class SubtitleChangeSegmenter:
    """State machine that separates subtitle timing from text recognition."""

    def __init__(self, change_threshold=0.08, min_segment_frames=1,
                 reference_alpha=0.15):
        self.change_threshold = max(0.0, float(change_threshold))
        self.min_segment_frames = max(1, int(min_segment_frames))
        self.reference_alpha = min(1.0, max(0.0, float(reference_alpha)))
        self._next_segment_id = 1
        self._active_id = None
        self._start_frame = None
        self._last_frame = None
        self._reference = None

    @staticmethod
    def signature_distance(first, second):
        if np is None:
            def flatten(value):
                if isinstance(value, (list, tuple)):
                    result = []
                    for item in value:
                        result.extend(flatten(item))
                    return result
                return [float(value)]

            first_values = flatten(first)
            second_values = flatten(second)
            if len(first_values) != len(second_values):
                raise ValueError("Signature shapes must match")
            if not first_values:
                return 0.0
            return sum(
                abs(a - b) for a, b in zip(first_values, second_values)
            ) / len(first_values)

        first = np.asarray(first, dtype=np.float32)
        second = np.asarray(second, dtype=np.float32)
        if first.shape != second.shape:
            if cv2 is None:
                raise ValueError(
                    "Signature shapes must match when OpenCV is unavailable"
                )
            second = cv2.resize(
                second,
                (first.shape[1], first.shape[0]),
                interpolation=cv2.INTER_AREA,
            )
        return float(np.mean(np.abs(first - second)))

    @staticmethod
    def _blend(first, second, alpha):
        if np is not None:
            return (
                (1.0 - alpha) * np.asarray(first, dtype=np.float32)
                + alpha * np.asarray(second, dtype=np.float32)
            )
        if isinstance(first, (list, tuple)):
            return [
                SubtitleChangeSegmenter._blend(a, b, alpha)
                for a, b in zip(first, second)
            ]
        return (1.0 - alpha) * float(first) + alpha * float(second)

    def _start(self, frame_no, signature):
        self._active_id = self._next_segment_id
        self._next_segment_id += 1
        self._start_frame = int(frame_no)
        self._last_frame = int(frame_no)
        if np is not None:
            self._reference = np.asarray(
                signature, dtype=np.float32
            ).copy()
        else:
            self._reference = deepcopy(signature)

    def _close_before(self, next_frame_no):
        # Split at the midpoint between the last old sample and the first new
        # sample. Ceil keeps two consecutive samples on their observed sides.
        new_start = (
            int(self._last_frame) + int(next_frame_no) + 1
        ) // 2
        end_frame = max(int(self._start_frame), new_start - 1)
        return SubtitleSegment(
            self._active_id, int(self._start_frame), end_frame
        ), new_start

    def _reset(self):
        self._active_id = None
        self._start_frame = None
        self._last_frame = None
        self._reference = None

    def observe(self, frame_no, signature, has_subtitle=True):
        """Consume one sampled frame and report starts/changes/ends."""
        frame_no = int(frame_no)
        if not has_subtitle:
            if self._active_id is None:
                return SegmentationDecision(None)
            closed, _ = self._close_before(frame_no)
            self._reset()
            return SegmentationDecision(None, closed)

        if self._active_id is None:
            self._start(frame_no, signature)
            return SegmentationDecision(self._active_id)

        score = self.signature_distance(self._reference, signature)
        old_enough = frame_no - int(self._start_frame) >= self.min_segment_frames
        if score >= self.change_threshold and old_enough:
            closed, new_start = self._close_before(frame_no)
            self._start(new_start, signature)
            # The logical boundary is interpolated between samples, while the
            # latest actual observation (and key-frame candidate) is frame_no.
            self._last_frame = frame_no
            return SegmentationDecision(
                self._active_id, closed_segment=closed, change_score=score
            )

        alpha = self.reference_alpha
        self._reference = self._blend(self._reference, signature, alpha)
        self._last_frame = frame_no
        return SegmentationDecision(self._active_id, change_score=score)

    def flush(self, final_frame=None):
        if self._active_id is None:
            return None
        end_frame = int(self._last_frame if final_frame is None else final_frame)
        end_frame = max(int(self._start_frame), end_frame)
        closed = SubtitleSegment(
            self._active_id, int(self._start_frame), end_frame
        )
        self._reset()
        return closed
