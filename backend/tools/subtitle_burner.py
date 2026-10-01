"""FFmpeg/libass subtitle burn-in worker used by the desktop UI."""

from collections import deque
from dataclasses import dataclass, replace
import html
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading

from backend.tools.ffmpeg_utils import (
    companion_ffprobe_path,
    encoder_works,
    has_filter,
    list_encoders,
    resolve_ffmpeg_path,
    subprocess_creation_flags,
)
from backend.tools.process_manager import ProcessManager


SUPPORTED_ENCODERS = ('auto', 'libx264', 'libx265', 'h264_nvenc', 'hevc_nvenc')
_SRT_TIMESTAMP = re.compile(
    r'^\s*(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})\s*-->\s*'
    r'(\d{1,2}:\d{2}:\d{2}[,.]\d{1,3})(?:\s+.*)?$'
)


class BurnError(RuntimeError):
    """A user-facing subtitle burn failure."""


class BurnCancelled(BurnError):
    """Raised when the user cancels an active burn."""


@dataclass(frozen=True)
class BurnOptions:
    video_path: str
    subtitle_path: str
    output_path: str
    font_name: str = 'Microsoft YaHei'
    font_size: int = 48
    margin_bottom: int = 40
    outline: float = 2.0
    shadow: float = 1.0
    color: str = '#FFFFFF'
    encoder: str = 'auto'
    bitrate_mode: str = 'source'
    quality: int = 20


@dataclass(frozen=True)
class BurnResult:
    output_path: str
    encoder: str
    audio_codec: str
    used_audio_fallback: bool
    used_encoder_fallback: bool


def _decode_subtitle(data):
    for encoding in ('utf-8-sig', 'utf-8', 'gb18030', 'utf-16', 'cp1252'):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BurnError('Unable to decode the SRT subtitle file.')


def _parse_srt(text):
    """Parse SRT cues while tolerating BOMs, CRLF and missing cue numbers."""
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    cues = []
    index = 0
    while index < len(lines):
        match = _SRT_TIMESTAMP.match(lines[index])
        if not match and index + 1 < len(lines):
            match = _SRT_TIMESTAMP.match(lines[index + 1])
            if match:
                index += 1
        if not match:
            index += 1
            continue
        start, end = match.groups()
        index += 1
        content = []
        while index < len(lines) and lines[index].strip():
            content.append(lines[index])
            index += 1
        cues.append((start, end, '\n'.join(content)))
    if not cues:
        raise BurnError('The SRT file contains no valid subtitle cues.')
    return cues


def _ass_timestamp(timestamp):
    hours, minutes, seconds = timestamp.replace(',', '.').split(':')
    seconds, milliseconds = seconds.split('.')
    total_ms = (
        int(hours) * 3_600_000 + int(minutes) * 60_000
        + int(seconds) * 1000 + int(milliseconds.ljust(3, '0')[:3])
    )
    centiseconds = int(round(total_ms / 10.0))
    hours, remainder = divmod(centiseconds, 360_000)
    minutes, remainder = divmod(remainder, 6_000)
    seconds, centiseconds = divmod(remainder, 100)
    return f'{hours}:{minutes:02d}:{seconds:02d}.{centiseconds:02d}'


def _ass_color(rgb):
    match = re.fullmatch(r'#?([0-9a-fA-F]{6})', rgb.strip())
    if not match:
        raise BurnError('Subtitle color must use #RRGGBB format.')
    value = match.group(1).upper()
    return f'&H00{value[4:6]}{value[2:4]}{value[0:2]}'


def _ass_text(text):
    text = re.sub(r'(?i)<br\s*/?>', '\n', text)
    text = re.sub(r'<[^>]+>', '', text)
    text = html.unescape(text)
    text = text.replace('\\', r'\\')
    text = text.replace('{', r'\{').replace('}', r'\}')
    return r'\N'.join(text.splitlines())


def convert_srt_to_ass(source_path, destination_path, options):
    """Convert SRT to a UTF-8 ASS document with one uniform style."""
    with open(source_path, 'rb') as source:
        cues = _parse_srt(_decode_subtitle(source.read()))

    font_name = options.font_name.replace('\r', ' ').replace('\n', ' ').replace(',', ' ').strip()
    if not font_name:
        raise BurnError('Font name cannot be empty.')
    color = _ass_color(options.color)
    header = (
        '[Script Info]\n'
        'ScriptType: v4.00+\n'
        'WrapStyle: 0\n'
        'ScaledBorderAndShadow: yes\n'
        'PlayResX: 1920\n'
        'PlayResY: 1080\n\n'
        '[V4+ Styles]\n'
        'Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, '
        'OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, '
        'ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, '
        'Alignment, MarginL, MarginR, MarginV, Encoding\n'
        f'Style: Default,{font_name},{int(options.font_size)},{color},'
        '&H000000FF,&H00000000,&H80000000,0,0,0,0,100,100,0,0,1,'
        f'{float(options.outline):g},{float(options.shadow):g},2,20,20,'
        f'{int(options.margin_bottom)},1\n\n'
        '[Events]\n'
        'Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, '
        'Effect, Text\n'
    )
    with open(destination_path, 'w', encoding='utf-8', newline='\n') as output:
        output.write(header)
        for start, end, text in cues:
            output.write(
                f'Dialogue: 0,{_ass_timestamp(start)},{_ass_timestamp(end)},'
                f'Default,,0,0,0,,{_ass_text(text)}\n'
            )


def validate_options(options):
    if not options.video_path.strip():
        raise BurnError('Choose an input video.')
    if not options.subtitle_path.strip():
        raise BurnError('Choose an SRT or ASS subtitle file.')
    if not options.output_path.strip():
        raise BurnError('Choose an output video path.')
    video_path = os.path.abspath(options.video_path)
    subtitle_path = os.path.abspath(options.subtitle_path)
    output_path = os.path.abspath(options.output_path)
    if not os.path.isfile(video_path):
        raise BurnError(f'Video file not found: {video_path}')
    if not os.path.isfile(subtitle_path):
        raise BurnError(f'Subtitle file not found: {subtitle_path}')
    extension = os.path.splitext(subtitle_path)[1].lower()
    if extension not in ('.srt', '.ass'):
        raise BurnError('Only SRT and ASS subtitle files are supported.')
    if video_path == output_path:
        raise BurnError('Output path must be different from the input video.')
    if not os.path.isdir(os.path.dirname(output_path)):
        raise BurnError(f'Output directory not found: {os.path.dirname(output_path)}')
    if not os.path.splitext(output_path)[1]:
        raise BurnError('Output path must include a video file extension.')
    if options.encoder not in SUPPORTED_ENCODERS:
        raise BurnError(f'Unsupported video encoder: {options.encoder}')
    if options.bitrate_mode not in ('source', 'quality'):
        raise BurnError(f'Unsupported bitrate mode: {options.bitrate_mode}')
    if not 0 <= int(options.quality) <= 51:
        raise BurnError('Quality must be between 0 and 51.')
    if int(options.font_size) <= 0 or int(options.margin_bottom) < 0:
        raise BurnError('Font size must be positive and bottom margin cannot be negative.')
    if float(options.outline) < 0 or float(options.shadow) < 0:
        raise BurnError('Outline and shadow cannot be negative.')
    _ass_color(options.color)
    return video_path, subtitle_path, output_path


def select_encoder(ffmpeg_path, requested='auto'):
    """Select a usable encoder, preferring real NVENC for Auto."""
    if requested == 'auto':
        if encoder_works(ffmpeg_path, 'h264_nvenc'):
            return 'h264_nvenc'
        requested = 'libx264'
    if requested not in list_encoders(ffmpeg_path):
        raise BurnError(f'FFmpeg does not provide the requested encoder: {requested}')
    if requested.endswith('_nvenc') and not encoder_works(ffmpeg_path, requested):
        raise BurnError(
            f'{requested} is installed but unavailable. Check the NVIDIA driver '
            'or choose a CPU encoder.'
        )
    return requested


def _probe_media_info(ffmpeg_path, video_path):
    probe = subprocess.run(
        [
            companion_ffprobe_path(ffmpeg_path), '-v', 'error',
            '-show_entries', 'format=duration,bit_rate:stream=codec_type,bit_rate',
            '-of', 'json', video_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        creationflags=subprocess_creation_flags(),
        check=False,
    )
    if probe.returncode != 0:
        return 0.0, 0
    try:
        info = json.loads(probe.stdout.decode('utf-8', errors='replace'))
        duration = max(0.0, float(info.get('format', {}).get('duration', 0)))
        video_bitrate = 0
        audio_bitrate = 0
        for stream in info.get('streams', []):
            try:
                bitrate = max(0, int(stream.get('bit_rate', 0)))
            except (TypeError, ValueError):
                bitrate = 0
            if stream.get('codec_type') == 'video' and not video_bitrate:
                video_bitrate = bitrate
            elif stream.get('codec_type') == 'audio':
                audio_bitrate += bitrate
        if not video_bitrate:
            try:
                total_bitrate = max(
                    0, int(info.get('format', {}).get('bit_rate', 0))
                )
            except (TypeError, ValueError):
                total_bitrate = 0
            video_bitrate = max(0, total_bitrate - audio_bitrate)
        return duration, video_bitrate
    except (ValueError, TypeError, json.JSONDecodeError):
        return 0.0, 0


def build_burn_command(
        ffmpeg_path, options, encoder, audio_codec,
        subtitle_name='subtitle.ass', source_video_bitrate=0):
    """Build an argv list. The libass path stays relative to an isolated cwd."""
    command = [
        ffmpeg_path, '-hide_banner', '-y', '-i', os.path.abspath(options.video_path),
        '-map', '0:v:0', '-map', '0:a?', '-vf', f'subtitles={subtitle_name}',
        '-c:v', encoder, '-pix_fmt', 'yuv420p',
    ]
    match_source = options.bitrate_mode == 'source' and source_video_bitrate > 0
    if encoder in ('libx264', 'libx265'):
        command.extend(['-preset', 'medium'])
    else:
        command.extend(['-preset', 'p5'])
    if match_source:
        target = int(source_video_bitrate)
        command.extend([
            '-b:v', str(target),
            '-maxrate', str(int(target * 1.35)),
            '-bufsize', str(target * 2),
        ])
        if encoder.endswith('_nvenc'):
            command.extend(['-rc', 'vbr'])
    elif encoder in ('libx264', 'libx265'):
        command.extend(['-crf', str(int(options.quality))])
    else:
        command.extend([
            '-rc', 'vbr', '-cq', str(int(options.quality)), '-b:v', '0',
        ])
    command.extend(['-c:a', audio_codec])
    if audio_codec == 'aac':
        command.extend(['-b:a', '192k'])
    if encoder in ('libx265', 'hevc_nvenc') and os.path.splitext(options.output_path)[1].lower() in ('.mp4', '.mov'):
        command.extend(['-tag:v', 'hvc1'])
    command.extend([
        '-map_metadata', '0', '-map_chapters', '0',
        '-progress', 'pipe:1', '-nostats', os.path.abspath(options.output_path),
    ])
    return command


class SubtitleBurner:
    """Synchronous worker intended to run inside a Python/Qt thread."""

    def __init__(self, options, progress=None, log=None):
        self.options = options
        self.progress = progress or (lambda _value: None)
        self.log = log or (lambda _message: None)
        self._cancelled = threading.Event()
        self._process = None
        self._process_id = None

    def cancel(self):
        self._cancelled.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except OSError:
                pass

    def _run_attempt(self, command, cwd, duration):
        if self._cancelled.is_set():
            raise BurnCancelled('Subtitle burn cancelled.')
        self.log(' '.join(command))
        tail = deque(maxlen=80)
        self._process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            errors='replace',
            bufsize=1,
            creationflags=subprocess_creation_flags(),
        )
        self._process_id = ProcessManager.instance().add_process(
            self._process, f'subtitle-burn-{id(self)}'
        )
        try:
            for raw_line in iter(self._process.stdout.readline, ''):
                line = raw_line.strip()
                if not line:
                    continue
                tail.append(line)
                if line.startswith(('out_time_us=', 'out_time_ms=')) and duration:
                    try:
                        elapsed = int(line.split('=', 1)[1]) / 1_000_000.0
                        self.progress(min(99, max(0, int(elapsed / duration * 100))))
                    except ValueError:
                        pass
                elif not line.startswith((
                    'frame=', 'fps=', 'stream_', 'bitrate=', 'total_size=',
                    'out_time=', 'dup_frames=', 'drop_frames=', 'speed=', 'progress=',
                )):
                    self.log(line)
                if self._cancelled.is_set() and self._process.poll() is None:
                    ProcessManager.instance().terminate_by_process(self._process)
            return_code = self._process.wait()
        finally:
            if self._process and self._process.stdout:
                self._process.stdout.close()
            if self._process_id:
                ProcessManager.instance().remove_process(self._process_id)
            self._process_id = None
            self._process = None
        if self._cancelled.is_set():
            raise BurnCancelled('Subtitle burn cancelled.')
        return return_code, '\n'.join(tail)

    def run(self):
        video_path, subtitle_path, output_path = validate_options(self.options)
        ffmpeg_path = resolve_ffmpeg_path()
        if not has_filter(ffmpeg_path, 'subtitles'):
            raise BurnError(
                'This FFmpeg build has no subtitles/libass filter. Install a '
                'full FFmpeg build with libass support.'
            )
        selected = select_encoder(ffmpeg_path, self.options.encoder)
        duration, source_video_bitrate = _probe_media_info(
            ffmpeg_path, video_path
        )
        if self.options.bitrate_mode == 'source':
            if source_video_bitrate:
                self.log(
                    'Matching source video bitrate: '
                    f'{source_video_bitrate / 1_000_000:.2f} Mbps'
                )
            else:
                self.log(
                    'Source video bitrate is unavailable; using quality mode.'
                )
        attempts = [(selected, 'copy')]
        attempts.append((selected, 'aac'))
        if self.options.encoder == 'auto' and selected == 'h264_nvenc':
            attempts.extend([('libx264', 'copy'), ('libx264', 'aac')])

        output_dir = os.path.dirname(output_path)
        output_name, output_extension = os.path.splitext(os.path.basename(output_path))
        partial_path = os.path.join(
            output_dir,
            f'.{output_name}.vse-part-{os.getpid()}-{id(self)}{output_extension}',
        )
        attempt_options = replace(self.options, output_path=partial_path)
        try:
            with tempfile.TemporaryDirectory(prefix='vse-burn-') as work_dir:
                local_subtitle = os.path.join(work_dir, 'subtitle.ass')
                if os.path.splitext(subtitle_path)[1].lower() == '.srt':
                    convert_srt_to_ass(subtitle_path, local_subtitle, self.options)
                else:
                    shutil.copyfile(subtitle_path, local_subtitle)

                failures = []
                for attempt_index, (encoder, audio_codec) in enumerate(attempts):
                    if attempt_index:
                        self.progress(0)
                        self.log(
                            f'Retrying with video={encoder}, audio={audio_codec}...'
                        )
                    command = build_burn_command(
                        ffmpeg_path, attempt_options, encoder, audio_codec,
                        source_video_bitrate=source_video_bitrate,
                    )
                    return_code, details = self._run_attempt(
                        command, work_dir, duration
                    )
                    if return_code == 0:
                        os.replace(partial_path, output_path)
                        self.progress(100)
                        return BurnResult(
                            output_path=output_path,
                            encoder=encoder,
                            audio_codec=audio_codec,
                            used_audio_fallback=audio_codec != 'copy',
                            used_encoder_fallback=encoder != selected,
                        )
                    failures.append(
                        f'video={encoder}, audio={audio_codec}\n{details}'
                    )

            raise BurnError(
                'FFmpeg could not burn the subtitles. Last attempts:\n\n'
                + '\n\n'.join(failures[-2:])
            )
        finally:
            try:
                os.remove(partial_path)
            except FileNotFoundError:
                pass
