"""Exercise the real SRT writer without loading Qt/Paddle/GPU dependencies."""
import ast
from difflib import SequenceMatcher
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest

from backend.tools.smart_pipeline import SmartSegmentSelection


def load_writer():
    source = Path(__file__).resolve().parents[1] / 'backend' / 'main.py'
    tree = ast.parse(source.read_text(encoding='utf-8'))
    extractor = next(n for n in tree.body if isinstance(n, ast.ClassDef)
                     and n.name == 'SubtitleExtractor')
    methods = [n for n in extractor.body if isinstance(n, ast.FunctionDef)
               and n.name in ('generate_subtitle_file_smart', '_frame_to_timecode')]
    namespace = {
        'config': SimpleNamespace(thresholdTextSimilarity=SimpleNamespace(value=80)),
        'ratio': lambda a, b: SequenceMatcher(None, a, b).ratio(),
        'tr': {'Main': {'SubLocation': '{}'}},
    }
    exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
    return type('Writer', (), {n.name: namespace[n.name] for n in methods})


class SmartSrtTests(unittest.TestCase):
    def write_srt(self, segments, raw, frames=60):
        with TemporaryDirectory() as directory:
            writer = load_writer()()
            writer.raw_subtitle_path = str(Path(directory) / 'raw.txt')
            writer.subtitle_output_path = str(Path(directory) / 'output.srt')
            Path(writer.raw_subtitle_path).write_text(raw, encoding='utf-8')
            writer.fps = 30
            writer.frame_count = frames
            writer.smart_pipeline_config = SimpleNamespace(scan_fps=8)
            writer.smart_segments = [SmartSegmentSelection(*s, 1.0) for s in segments]
            writer._concat_content_with_same_frameno = lambda: None
            writer.append_output = lambda *_: None
            writer.generate_subtitle_file_smart()
            return Path(writer.subtitle_output_path).read_text(encoding='utf-8')

    def test_srt_blocks_and_one_based_frame_boundaries(self):
        result = self.write_srt([(1, 30, 1), (31, 60, 31)],
                                '1\tbox\tHello\n31\tbox\tWorld\n')
        self.assertEqual(result, '1\n00:00:00,000 --> 00:00:01,000\nHello\n\n'
                                 '2\n00:00:01,000 --> 00:00:02,000\nWorld\n\n')

    def test_single_final_frame_has_positive_duration(self):
        result = self.write_srt([(60, 60, 60)], '60\tbox\tLast\n')
        self.assertEqual(result, '1\n00:00:01,966 --> 00:00:02,000\nLast\n\n')

    def test_duplicate_text_merges_and_empty_ocr_is_skipped(self):
        result = self.write_srt([(1, 15, 1), (16, 30, 16), (31, 60, 31)],
                                '1\tbox\tSame\n16\tbox\tSame\n31\tbox\t\n')
        self.assertEqual(result, '1\n00:00:00,000 --> 00:00:01,000\nSame\n\n')


if __name__ == '__main__':
    unittest.main()
