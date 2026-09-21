import unittest

from backend.tools.ocr_backend import (
    OcrBackend,
    available_ocr_backends,
    create_ocr_backend,
    register_ocr_backend,
)


class FakeBackend(OcrBackend):
    name = "test-fake"

    def __init__(self, marker=None):
        self.marker = marker

    def predict(self, image):
        return [image], [(self.marker, 1.0)]


class OcrBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        register_ocr_backend(
            "test-fake", lambda **kwargs: FakeBackend(**kwargs), replace=True
        )

    def test_registered_backend_is_created_without_importing_paddle(self):
        backend = create_ocr_backend("TEST-FAKE", marker="ok")
        self.assertIsInstance(backend, OcrBackend)
        self.assertEqual(backend.marker, "ok")
        self.assertIn("paddle", available_ocr_backends())
        self.assertIn("test-fake", available_ocr_backends())

    def test_unknown_backend_has_actionable_error(self):
        with self.assertRaisesRegex(ValueError, "Unknown OCR backend"):
            create_ocr_backend("does-not-exist")


if __name__ == "__main__":
    unittest.main()
