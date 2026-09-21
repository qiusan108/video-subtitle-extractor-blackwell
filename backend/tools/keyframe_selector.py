"""Key-frame selection interfaces for subtitle segments."""

from abc import ABC, abstractmethod
from dataclasses import dataclass

try:
    import numpy as np
except ImportError:  # Selection has a small pure-Python test/runtime fallback.
    np = None

try:
    import cv2
except ImportError:  # Keep selector logic testable without runtime extras.
    cv2 = None


@dataclass(frozen=True)
class KeyFrame:
    frame_no: int
    score: float


class KeyFrameSelector(ABC):
    @abstractmethod
    def reset(self):
        """Discard candidates from the previous segment."""

    @abstractmethod
    def consider(self, frame_no, image):
        """Consider one image as the representative for the active segment."""

    @abstractmethod
    def select(self):
        """Return the best candidate or ``None``."""


class SharpestFrameSelector(KeyFrameSelector):
    """Choose the least blurred frame using Laplacian variance."""

    def __init__(self):
        self.reset()

    def reset(self):
        self._best = None

    @staticmethod
    def score(image):
        if image is None:
            return float("-inf")
        if hasattr(image, 'size') and image.size == 0:
            return float("-inf")
        if np is None:
            rows = [list(map(float, row)) for row in image]
            if not rows or not rows[0]:
                return float("-inf")
            differences = []
            for row in rows:
                differences.extend(
                    row[index + 1] - row[index]
                    for index in range(len(row) - 1)
                )
            for row_index in range(len(rows) - 1):
                differences.extend(
                    rows[row_index + 1][column] - rows[row_index][column]
                    for column in range(len(rows[0]))
                )
            if not differences:
                return 0.0
            mean = sum(differences) / len(differences)
            return sum((value - mean) ** 2 for value in differences) / len(
                differences
            )
        image = np.asarray(image)
        if image.ndim == 3 and cv2 is not None:
            image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        elif image.ndim == 3:
            image = image.astype(np.float64).mean(axis=2)
        if cv2 is not None:
            return float(cv2.Laplacian(image, cv2.CV_64F).var())
        image = image.astype(np.float64)
        horizontal = np.diff(image, axis=1)
        vertical = np.diff(image, axis=0)
        return float(horizontal.var() + vertical.var())

    def consider(self, frame_no, image):
        candidate = KeyFrame(int(frame_no), self.score(image))
        if self._best is None or candidate.score > self._best.score:
            self._best = candidate

    def select(self):
        return self._best
