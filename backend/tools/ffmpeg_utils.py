"""Shared FFmpeg discovery and capability helpers."""

import glob
import os
import shutil
import subprocess
from functools import lru_cache


def subprocess_creation_flags():
    """Keep helper command windows hidden on Windows."""
    if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        return subprocess.CREATE_NO_WINDOW
    return 0


def resolve_ffmpeg_path():
    """Find FFmpeg in configured, PATH, project-local and common locations."""
    configured = os.environ.get('VSE_FFMPEG_PATH', '').strip().strip('"')
    candidates = [
        configured,
        shutil.which('ffmpeg'),
        shutil.which('ffmpeg.exe'),
    ]
    for candidate in candidates:
        if candidate and os.path.isfile(candidate):
            return os.path.abspath(candidate)

    project_root = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))
    ai_root = os.path.dirname(project_root)
    search_roots = [
        os.path.join(project_root, 'ffmpeg'),
        os.path.join(ai_root, 'ffmpeg'),
    ]
    if os.name == 'nt':
        search_roots.extend([
            os.path.join(os.environ.get('ProgramFiles', ''), 'ffmpeg'),
            r'C:\ffmpeg',
            r'D:\ffmpeg',
        ])

    executable = 'ffmpeg.exe' if os.name == 'nt' else 'ffmpeg'
    for search_root in search_roots:
        if not os.path.isdir(search_root):
            continue
        matches = sorted(glob.glob(
            os.path.join(search_root, '**', executable), recursive=True
        ))
        if matches:
            resolved = os.path.abspath(matches[0])
            print(f'VSE auto-discovered FFmpeg: {resolved}')
            return resolved

    raise RuntimeError(
        'FFmpeg was not found. Set VSE_FFMPEG_PATH to ffmpeg.exe '
        'or place FFmpeg in the project or a sibling ffmpeg directory.'
    )


def companion_ffprobe_path(ffmpeg_path):
    """Return the ffprobe executable installed beside FFmpeg."""
    name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    path = os.path.join(os.path.dirname(ffmpeg_path), name)
    if not os.path.isfile(path):
        found = shutil.which(name)
        if found:
            return os.path.abspath(found)
        raise RuntimeError(f'ffprobe was not found beside FFmpeg: {path}')
    return path


@lru_cache(maxsize=8)
def list_encoders(ffmpeg_path):
    """Return encoder names advertised by an FFmpeg executable."""
    probe = subprocess.run(
        [ffmpeg_path, '-hide_banner', '-encoders'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    if probe.returncode != 0:
        return set()
    encoders = set()
    for raw_line in probe.stdout.decode('utf-8', errors='replace').splitlines():
        parts = raw_line.split()
        if len(parts) >= 2 and len(parts[0]) == 6:
            encoders.add(parts[1])
    return encoders


@lru_cache(maxsize=8)
def has_filter(ffmpeg_path, filter_name):
    """Return whether FFmpeg advertises one named video/audio filter."""
    probe = subprocess.run(
        [ffmpeg_path, '-hide_banner', '-filters'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    if probe.returncode != 0:
        return False
    for raw_line in probe.stdout.decode('utf-8', errors='replace').splitlines():
        parts = raw_line.split()
        if len(parts) >= 2 and parts[1] == filter_name:
            return True
    return False


@lru_cache(maxsize=16)
def encoder_works(ffmpeg_path, encoder):
    """Run a one-frame encode so listed-but-unusable NVENC is not selected."""
    if encoder not in list_encoders(ffmpeg_path):
        return False
    probe = subprocess.run(
        [
            ffmpeg_path, '-hide_banner', '-loglevel', 'error',
            # 256x256 is above the minimum frame dimensions enforced by
            # current Blackwell NVENC drivers.
            '-f', 'lavfi', '-i', 'color=size=256x256:rate=1:duration=0.1',
            '-frames:v', '1', '-an', '-c:v', encoder, '-f', 'null', '-',
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    return probe.returncode == 0
