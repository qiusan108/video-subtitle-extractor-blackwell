import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

from backend.tools.ffmpeg_utils import resolve_ffmpeg_path, subprocess_creation_flags
from backend.tools.subtitle_burner import (
    BurnCancelled,
    BurnOptions,
    SubtitleBurner,
    build_burn_command,
    convert_srt_to_ass,
    select_encoder,
)


class SubtitleBurnerLogicTests(unittest.TestCase):
    def test_srt_conversion_applies_uniform_ass_style(self):
        with tempfile.TemporaryDirectory() as directory:
            source = os.path.join(directory, '字幕 file.srt')
            output = os.path.join(directory, 'subtitle.ass')
            with open(source, 'w', encoding='utf-8-sig') as subtitle:
                subtitle.write(
                    '1\n00:00:00,100 --> 00:00:01,250\n你好 <b>world</b>\n第二行\n'
                )
            options = BurnOptions(
                'input.mp4', source, 'output.mp4', font_name='思源 黑体',
                font_size=52, margin_bottom=66, outline=3.5,
                shadow=1.5, color='#12ABEF',
            )

            convert_srt_to_ass(source, output, options)

            with open(output, encoding='utf-8') as converted:
                contents = converted.read()
            self.assertIn('Style: Default,思源 黑体,52,&H00EFAB12', contents)
            self.assertIn(',3.5,1.5,2,20,20,66,1', contents)
            self.assertIn('Dialogue: 0,0:00:00.10,0:00:01.25', contents)
            self.assertIn(r'你好 world\N第二行', contents)

    def test_command_keeps_user_paths_out_of_filter_expression(self):
        options = BurnOptions(
            r'C:\视频 文件\input [1].mp4',
            r'C:\字幕 文件\sub,one.srt',
            r'C:\输出 文件\result [ok].mp4',
            encoder='libx264', quality=19,
        )
        command = build_burn_command('ffmpeg.exe', options, 'libx264', 'copy')

        self.assertIn(os.path.abspath(options.video_path), command)
        self.assertIn(os.path.abspath(options.output_path), command)
        self.assertEqual(command[command.index('-vf') + 1], 'subtitles=subtitle.ass')
        self.assertNotIn(options.subtitle_path, ' '.join(command))
        self.assertEqual(command[command.index('-crf') + 1], '19')

    @mock.patch('backend.tools.subtitle_burner.list_encoders', return_value={'libx264'})
    @mock.patch('backend.tools.subtitle_burner.encoder_works', return_value=False)
    def test_auto_encoder_falls_back_to_cpu(self, _works, _encoders):
        self.assertEqual(select_encoder('ffmpeg', 'auto'), 'libx264')

    def test_running_process_can_be_cancelled(self):
        worker = SubtitleBurner(BurnOptions('input.mp4', 'input.srt', 'output.mp4'))
        outcome = []

        def run():
            try:
                worker._run_attempt(
                    [sys.executable, '-c', 'import time; time.sleep(30)'],
                    os.getcwd(), 30,
                )
            except Exception as error:
                outcome.append(error)

        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.time() + 5
        while worker._process is None and time.time() < deadline:
            time.sleep(0.01)
        worker.cancel()
        thread.join(5)

        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcome), 1)
        self.assertIsInstance(outcome[0], BurnCancelled)


class SubtitleBurnerFFmpegIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg = resolve_ffmpeg_path()
        except RuntimeError as error:
            raise unittest.SkipTest(str(error))
        filters = subprocess.run(
            [cls.ffmpeg, '-hide_banner', '-filters'],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            creationflags=subprocess_creation_flags(), check=False,
        ).stdout.decode('utf-8', errors='replace')
        if not any(' subtitles ' in line for line in filters.splitlines()):
            raise unittest.SkipTest('FFmpeg was built without the subtitles/libass filter')

    def test_real_burn_handles_unicode_spaces_and_audio_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            special = os.path.join(directory, '中文 空格 [special]')
            os.mkdir(special)
            source = os.path.join(special, '输入 video.mkv')
            subtitle = os.path.join(special, '字幕 #1.srt')
            output = os.path.join(special, '输出 burned.mp4')
            generated = subprocess.run(
                [
                    self.ffmpeg, '-hide_banner', '-loglevel', 'error', '-y',
                    '-f', 'lavfi', '-i', 'color=c=blue:size=320x180:rate=24:duration=1',
                    '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                    '-c:v', 'libx264', '-c:a', 'wmav2', '-shortest', source,
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                creationflags=subprocess_creation_flags(), check=False,
            )
            if generated.returncode:
                self.fail(generated.stderr.decode('utf-8', errors='replace'))
            with open(subtitle, 'w', encoding='utf-8') as subtitles:
                subtitles.write('1\n00:00:00,100 --> 00:00:00,900\n中文 burn test\n')

            expected_encoder = select_encoder(self.ffmpeg, 'auto')
            result = SubtitleBurner(BurnOptions(
                source, subtitle, output, encoder='auto', quality=28,
            )).run()

            self.assertTrue(os.path.isfile(output))
            self.assertGreater(os.path.getsize(output), 1000)
            self.assertEqual(result.encoder, expected_encoder)
            self.assertEqual(result.audio_codec, 'aac')
            self.assertTrue(result.used_audio_fallback)
            self.assertFalse(any('vse-part' in name for name in os.listdir(special)))


if __name__ == '__main__':
    unittest.main()
