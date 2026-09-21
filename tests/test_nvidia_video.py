import os
import sys
import unittest


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from backend.tools.nvidia_video import (  # noqa: E402
    parse_cuvid_decoders,
    parse_nvidia_smi_csv,
    select_cuvid_decoder,
)


class NvidiaGpuTests(unittest.TestCase):
    def test_parses_rtx_50_series_gpu(self):
        gpu = parse_nvidia_smi_csv(
            'NVIDIA GeForce RTX 5060, 8151 MiB, 610.62, 12.0\n'
        )[0]
        self.assertEqual(gpu.memory_mib, 8151)
        self.assertEqual(gpu.compute_capability, '12.0')
        self.assertTrue(gpu.is_rtx_50_series)

    def test_detects_available_decoders(self):
        decoders = parse_cuvid_decoders(
            ' V..... h264_cuvid Nvidia decoder\n V..... av1_cuvid Nvidia decoder\n'
        )
        self.assertEqual(decoders, {'h264_cuvid', 'av1_cuvid'})
        self.assertEqual(select_cuvid_decoder('h264', decoders), 'h264_cuvid')
        with self.assertRaises(RuntimeError):
            select_cuvid_decoder('hevc', decoders)

    def test_rejects_unknown_codec(self):
        with self.assertRaises(RuntimeError):
            select_cuvid_decoder('prores', set())


if __name__ == '__main__':
    unittest.main()
