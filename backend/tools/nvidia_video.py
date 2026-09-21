"""Small, dependency-free helpers for NVIDIA GPU and NVDEC discovery."""

from __future__ import annotations

import csv
import re
import subprocess
from dataclasses import dataclass
from io import StringIO


CUVID_DECODER_BY_CODEC = {
    'h264': 'h264_cuvid',
    'hevc': 'hevc_cuvid',
    'av1': 'av1_cuvid',
    'vp8': 'vp8_cuvid',
    'vp9': 'vp9_cuvid',
    'mpeg1video': 'mpeg1_cuvid',
    'mpeg2video': 'mpeg2_cuvid',
    'mpeg4': 'mpeg4_cuvid',
    'vc1': 'vc1_cuvid',
    'mjpeg': 'mjpeg_cuvid',
}


@dataclass(frozen=True)
class NvidiaGpu:
    name: str
    memory_mib: int | None = None
    driver_version: str = ''
    compute_capability: str = ''

    @property
    def is_rtx_50_series(self) -> bool:
        return bool(re.search(r'\bRTX\s+50\d{2}\b', self.name, re.IGNORECASE))


def parse_nvidia_smi_csv(text: str) -> list[NvidiaGpu]:
    """Parse name, memory, driver and compute-capability CSV output."""
    gpus = []
    for row in csv.reader(StringIO(text), skipinitialspace=True):
        if not row or not row[0].strip():
            continue
        fields = [value.strip() for value in row]
        memory_match = re.search(r'\d+', fields[1]) if len(fields) > 1 else None
        gpus.append(NvidiaGpu(
            name=fields[0],
            memory_mib=int(memory_match.group()) if memory_match else None,
            driver_version=fields[2] if len(fields) > 2 else '',
            compute_capability=fields[3] if len(fields) > 3 else '',
        ))
    return gpus


def query_nvidia_gpus(timeout: float = 5.0) -> list[NvidiaGpu]:
    result = subprocess.run(
        [
            'nvidia-smi',
            '--query-gpu=name,memory.total,driver_version,compute_cap',
            '--format=csv,noheader',
        ],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )
    if result.returncode != 0:
        return []
    return parse_nvidia_smi_csv(result.stdout)


def parse_cuvid_decoders(text: str) -> set[str]:
    return set(re.findall(r'\b([a-z0-9]+_cuvid)\b', text.lower()))


def select_cuvid_decoder(codec: str, available: set[str] | None = None) -> str:
    normalized = str(codec or '').strip().lower()
    decoder = CUVID_DECODER_BY_CODEC.get(normalized)
    if decoder is None:
        raise RuntimeError(f'no configured CUVID decoder for codec: {normalized or "unknown"}')
    if available is not None and decoder not in available:
        raise RuntimeError(
            f'FFmpeg does not expose {decoder} for source codec {normalized}'
        )
    return decoder
