"""First-stage Smart/Accurate subtitle candidate pipeline."""

from dataclasses import dataclass
import os

from backend.tools.keyframe_selector import SharpestFrameSelector
from backend.tools.subtitle_segmenter import (
    SubtitleChangeSegmenter,
    SubtitleFrameAnalyzer,
)


def _env_float(name, default, minimum, maximum):
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = float(default)
    return min(maximum, max(minimum, value))


@dataclass(frozen=True)
class SmartPipelineConfig:
    scan_fps: float = 8.0
    change_threshold: float = 0.08
    min_edge_density: float = 0.004
    min_segment_ms: float = 250.0

    @classmethod
    def from_environment(cls):
        return cls(
            scan_fps=_env_float("VSE_SMART_SCAN_FPS", 8.0, 1.0, 30.0),
            change_threshold=_env_float(
                "VSE_SMART_CHANGE_THRESHOLD", 0.08, 0.005, 1.0
            ),
            min_edge_density=_env_float(
                "VSE_SMART_MIN_EDGE_DENSITY", 0.004, 0.0, 0.5
            ),
            min_segment_ms=_env_float(
                "VSE_SMART_MIN_SEGMENT_MS", 250.0, 0.0, 5000.0
            ),
        )


@dataclass(frozen=True)
class SmartSegmentSelection:
    start_frame: int
    end_frame: int
    keyframe_no: int
    keyframe_score: float


class SmartSubtitleScanner:
    """Scan an already-open video and return one key frame per segment."""

    def __init__(self, config=None, analyzer=None, segmenter=None,
                 selector_factory=SharpestFrameSelector):
        self.config = config or SmartPipelineConfig.from_environment()
        self.analyzer = analyzer or SubtitleFrameAnalyzer(
            min_edge_density=self.config.min_edge_density
        )
        self.segmenter = segmenter
        self.selector_factory = selector_factory

    @staticmethod
    def _crop(frame, area):
        if area is None:
            return frame
        height, width = frame.shape[:2]
        xmin = max(0, min(width, int(area.xmin)))
        xmax = max(0, min(width, int(area.xmax)))
        ymin = max(0, min(height, int(area.ymin)))
        ymax = max(0, min(height, int(area.ymax)))
        if xmin >= xmax or ymin >= ymax:
            return None
        return frame[ymin:ymax, xmin:xmax]

    def scan(self, capture, fps, total_frames, area=None, progress=None):
        fps = max(0.001, float(fps))
        sample_interval = max(1, round(fps / self.config.scan_fps))
        min_segment_frames = max(
            1, round(fps * self.config.min_segment_ms / 1000.0)
        )
        segmenter = self.segmenter or SubtitleChangeSegmenter(
            change_threshold=self.config.change_threshold,
            min_segment_frames=min_segment_frames,
        )
        selector = self.selector_factory()
        selections = []
        current_frame = 0

        def finish_segment(segment):
            keyframe = selector.select()
            if segment is not None and keyframe is not None:
                selections.append(SmartSegmentSelection(
                    start_frame=segment.start_frame,
                    end_frame=segment.end_frame,
                    keyframe_no=keyframe.frame_no,
                    keyframe_score=keyframe.score,
                ))
            selector.reset()

        while capture.isOpened():
            ok, frame = capture.read()
            if not ok:
                break
            current_frame += 1
            if (current_frame - 1) % sample_interval:
                continue
            roi = self._crop(frame, area)
            analysis = self.analyzer.analyze(roi)
            decision = segmenter.observe(
                current_frame, analysis.signature, analysis.has_subtitle
            )
            if decision.closed_segment is not None:
                finish_segment(decision.closed_segment)
            if decision.current_segment_id is not None:
                selector.consider(current_frame, roi)
            if progress is not None:
                progress(current_frame, total_frames)

        finish_segment(segmenter.flush(current_frame))
        return selections
