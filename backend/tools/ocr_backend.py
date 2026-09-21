"""Pluggable OCR backend contract.

The extraction pipeline only depends on this small interface.  The PaddleOCR
implementation remains the default and is imported lazily so future backends
can be added without coupling video segmentation to Paddle.
"""

from abc import ABC, abstractmethod
import os


class OcrBackend(ABC):
    """Minimal OCR interface used by subtitle extraction workers."""

    name = "unknown"

    @abstractmethod
    def predict(self, image):
        """Return ``(detection_boxes, recognition_results)`` for one image."""

    def predict_batch(self, images):
        """Recognise a batch while preserving input order."""
        return [self.predict(image) for image in images]

    def prepare(self):
        """Optionally allocate and warm up inference resources."""
        return self

    def release(self):
        """Optionally release inference resources."""


_BACKEND_FACTORIES = {}


def register_ocr_backend(name, factory, *, replace=False):
    """Register an OCR backend factory under a stable, case-insensitive name."""
    normalized = str(name).strip().lower()
    if not normalized:
        raise ValueError("OCR backend name cannot be empty")
    if normalized in _BACKEND_FACTORIES and not replace:
        raise ValueError(f"OCR backend already registered: {normalized}")
    if not callable(factory):
        raise TypeError("OCR backend factory must be callable")
    _BACKEND_FACTORIES[normalized] = factory


def available_ocr_backends():
    """Return the available built-in and registered backend names."""
    return tuple(sorted({"paddle", *_BACKEND_FACTORIES.keys()}))


def create_ocr_backend(name=None, **kwargs):
    """Create the configured OCR backend.

    ``VSE_OCR_BACKEND`` is intentionally optional.  With no configuration the
    exact historical PaddleOCR path is used.
    """
    normalized = (name or os.environ.get("VSE_OCR_BACKEND", "paddle"))
    normalized = str(normalized).strip().lower()
    if normalized == "paddle":
        # Lazy import avoids importing Paddle for pure segmentation/tests.
        from backend.tools.ocr import PaddleOcrBackend

        return PaddleOcrBackend(**kwargs)
    try:
        factory = _BACKEND_FACTORIES[normalized]
    except KeyError as error:
        choices = ", ".join(available_ocr_backends())
        raise ValueError(
            f"Unknown OCR backend '{normalized}'. Available: {choices}"
        ) from error
    backend = factory(**kwargs)
    if not isinstance(backend, OcrBackend):
        raise TypeError(
            f"OCR backend factory '{normalized}' did not return OcrBackend"
        )
    return backend
