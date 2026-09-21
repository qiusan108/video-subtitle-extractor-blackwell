import os
import re
import time
import subprocess
from multiprocessing import Queue, Process
import cv2
from PIL import ImageFont, ImageDraw, Image
from tqdm import tqdm
from backend.tools.ocr import OcrRecogniser, get_coordinates
from backend.tools.constant import SubtitleArea
from backend.tools import constant
from threading import Thread
import threading
from concurrent.futures import ThreadPoolExecutor
import queue
from types import SimpleNamespace
import shutil
import glob
import numpy as np
from collections import namedtuple, deque
from backend.config import tr
from backend.tools.nvidia_video import (
    parse_cuvid_decoders,
    select_cuvid_decoder,
)

TURBO_BLANK_MARKER = '__VSE_TURBO_BLANK__'

OCR_FRAME_BATCH_SIZE = max(1, int(os.environ.get('VSE_OCR_FRAME_BATCH_SIZE', '1')))
OCR_WORKERS = max(1, min(2, int(os.environ.get('VSE_OCR_WORKERS', '1'))))
OCR_BATCH_WAIT_SECONDS = max(
    0.0, float(os.environ.get('VSE_OCR_BATCH_WAIT_MS', '0')) / 1000.0
)
USE_VSF_RGB_IMAGES = os.environ.get(
    'VSE_USE_VSF_RGB_IMAGES', '1'
).strip().lower() not in ('0', 'false', 'no', 'off')
VSF_RGB_READ_RETRIES = max(
    1, min(20, int(os.environ.get('VSE_VSF_RGB_READ_RETRIES', '5')))
)
VSF_RGB_RETRY_SECONDS = max(
    0.0, float(os.environ.get('VSE_VSF_RGB_RETRY_MS', '10')) / 1000.0
)
DECODE_WORKERS = max(
    1, min(8, int(os.environ.get('VSE_DECODE_WORKERS', '3')))
)
DECODE_PREFETCH = max(
    DECODE_WORKERS,
    min(128, int(os.environ.get('VSE_DECODE_PREFETCH', '40')))
)
DECODE_COLLECT_WAIT_SECONDS = max(
    0.0, float(os.environ.get('VSE_DECODE_COLLECT_WAIT_MS', '2')) / 1000.0
)


def _subprocess_creation_flags():
    """Keep helper FFmpeg/FFprobe windows hidden on Windows."""
    if os.name == 'nt' and hasattr(subprocess, 'CREATE_NO_WINDOW'):
        return subprocess.CREATE_NO_WINDOW
    return 0


def _resolve_ffmpeg_path():
    """
    Resolve FFmpeg without relying only on inherited environment variables.

    VSE is often launched from Explorer, so a newly-written user environment
    variable may not be visible until Explorer or Windows is restarted.  Search
    project-local, sibling, PATH, and common Windows FFmpeg locations.
    """
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

    for search_root in search_roots:
        if not os.path.isdir(search_root):
            continue
        matches = sorted(glob.glob(
            os.path.join(search_root, '**', 'ffmpeg.exe'),
            recursive=True,
        ))
        if matches:
            resolved = os.path.abspath(matches[0])
            print(f'VSE auto-discovered FFmpeg: {resolved}')
            return resolved

    raise RuntimeError(
        'FFmpeg was not found. Set VSE_FFMPEG_PATH to ffmpeg.exe '
        'or place FFmpeg in the project or a sibling ffmpeg directory.'
    )


def _probe_cuvid_decoder(ffmpeg_path, video_path):
    """Return the matching CUVID decoder for the first video stream."""
    ffprobe_name = 'ffprobe.exe' if os.name == 'nt' else 'ffprobe'
    ffprobe_path = os.path.join(os.path.dirname(ffmpeg_path), ffprobe_name)
    if not os.path.isfile(ffprobe_path):
        raise RuntimeError(f'ffprobe was not found beside FFmpeg: {ffprobe_path}')

    probe = subprocess.run(
        [
            ffprobe_path,
            '-v', 'error',
            '-select_streams', 'v:0',
            '-show_entries', 'stream=codec_name',
            '-of', 'default=noprint_wrappers=1:nokey=1',
            video_path,
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
        creationflags=_subprocess_creation_flags(),
        check=False,
    )
    if probe.returncode != 0:
        message = probe.stderr.decode('utf-8', errors='replace').strip()
        raise RuntimeError(f'ffprobe failed: {message}')

    codec = probe.stdout.decode('utf-8', errors='replace').strip().lower()
    decoder_probe = subprocess.run(
        [ffmpeg_path, '-hide_banner', '-decoders'],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=30,
        creationflags=_subprocess_creation_flags(),
        check=False,
    )
    if decoder_probe.returncode != 0:
        raise RuntimeError('FFmpeg decoder capability probe failed')
    available_decoders = parse_cuvid_decoders(
        decoder_probe.stdout.decode('utf-8', errors='replace')
    )
    decoder = select_cuvid_decoder(codec, available_decoders)
    return codec, decoder


def _read_exact_bytes(stream, size):
    """Read one fixed-size raw frame, tolerating short pipe reads."""
    chunks = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining:
        return b''
    return b''.join(chunks)


def extract_subtitles(data, text_recogniser, img, raw_subtitles,
                      sub_area, options, dt_box_arg, rec_res_arg, ocr_loss_debug_path):
    """
    提取视频帧中的字幕信息
    """
    # 从参数中获取检测框与检测结果
    dt_box = dt_box_arg
    rec_res = rec_res_arg
    # 如果没有检测结果，则获取检测结果
    if dt_box is None or rec_res is None:
        dt_box, rec_res = text_recogniser.predict(img)
        # rec_res格式为： ("hello", 0.997)
    # 获取文本坐标
    coordinates = get_coordinates(dt_box)
    # 将结果写入txt文本中
    if options.REC_CHAR_TYPE == 'en':
        # 如果识别语言为英文，则去除中文
        text_res = [(re.sub('[\u4e00-\u9fa5]', '', res[0]), res[1]) for res in rec_res]
    else:
        text_res = [(res[0], res[1]) for res in rec_res]
    line = ''
    loss_list = []
    for content, coordinate in zip(text_res, coordinates):
        text = content[0]
        prob = content[1]
        if sub_area is not None:
            selected = False
            # 初始化超界偏差为0
            overflow_area_rate = 0
            # 使用AABB矩形重叠判断（比Shapely Polygon快得多）
            c_xmin, c_xmax, c_ymin, c_ymax = coordinate
            # 计算交集矩形
            inter_xmin = max(sub_area.xmin, c_xmin)
            inter_ymin = max(sub_area.ymin, c_ymin)
            inter_xmax = min(sub_area.xmax, c_xmax)
            inter_ymax = min(sub_area.ymax, c_ymax)
            has_intersection = inter_xmin < inter_xmax and inter_ymin < inter_ymax
            drop_reason = ''
            # 如果有交集
            if has_intersection:
                sub_area_w = sub_area.xmax - sub_area.xmin
                sub_area_h = sub_area.ymax - sub_area.ymin
                sub_area_size = sub_area_w * sub_area_h
                inter_area = (inter_xmax - inter_xmin) * (inter_ymax - inter_ymin)
                coord_area = (c_xmax - c_xmin) * (c_ymax - c_ymin)
                # 计算越界允许偏差
                overflow_area_rate = ((sub_area_size + coord_area - inter_area) / sub_area_size) - 1
                # 如果越界比例低于设定阈值且该行文本识别的置信度高于设定阈值
                not_overflow = overflow_area_rate <= options.SUB_AREA_DEVIATION_RATE
                confident = prob > options.DROP_SCORE
                if not_overflow and confident:
                    # 保留该帧
                    selected = True
                    line += f'{str(data["i"]).zfill(8)}\t{coordinate}\t{text}\n'
                    raw_subtitles.append(f'{str(data["i"]).zfill(8)}\t{coordinate}\t{text}\n')
                else:
                    if not not_overflow:
                        drop_reason = tr['Main']['OcrDropOutOfBoxRate'].format(int(options.SUB_AREA_DEVIATION_RATE * 100), int(overflow_area_rate * 100))
                    elif not confident:
                        drop_reason = tr['Main']['OcrDropConfidentLow'].format(int(options.DROP_SCORE * 100))
            else:
                drop_reason = tr['Main']['OcrDropNoIntercetion']
            # Per-result terminal output is surprisingly expensive on videos
            # with many candidates.  Keep it available only in OCR-loss debug
            # mode; normal extraction remains quiet and faster.
            if options.DEBUG_OCR_LOSS:
                if drop_reason:
                    tqdm.write(tr['Main']['OcrResultWithDropReason'].format(text, round(prob * 100,1), drop_reason))
                else:
                    tqdm.write(tr['Main']['OcrResult'].format(text, round(prob * 100,1)))
            # 保存丢掉的识别结果
            loss_info = namedtuple('loss_info', 'text prob overflow_area_rate coordinate selected')
            loss_list.append(loss_info(text, prob, overflow_area_rate, coordinate, selected))
        else:
            raw_subtitles.append(f'{str(data["i"]).zfill(8)}\t{coordinate}\t{text}\n')
    # 输出调试信息
    dump_debug_info(options, line, img, loss_list, ocr_loss_debug_path, sub_area, data)


def dump_debug_info(options, line, img, loss_list, ocr_loss_debug_path, sub_area, data):
    loss = False
    if options.DEBUG_OCR_LOSS and options.REC_CHAR_TYPE in ('ch', 'japan ', 'korea', 'ch_tra'):
        loss = len(line) > 0 and re.search(r'[\u4e00-\u9fa5\u3400-\u4db5\u3130-\u318F\uAC00-\uD7A3\u0800-\u4e00]', line) is None
    if loss:
        if not os.path.exists(ocr_loss_debug_path):
            os.makedirs(ocr_loss_debug_path, mode=0o777, exist_ok=True)
        img = cv2.rectangle(img, (sub_area.xmin, sub_area.ymin), (sub_area.xmax, sub_area.ymax), constant.BGR_COLOR_BLUE, 2)
        for loss_info in loss_list:
            coordinate = loss_info.coordinate
            color = constant.BGR_COLOR_GREEN if loss_info.selected else constant.BGR_COLOR_RED
            text = f"[{loss_info.text}] prob:{loss_info.prob:.4f} or:{loss_info.overflow_area_rate:.2f}"
            img = paint_chinese_opencv(img, text, pos=(coordinate[0], coordinate[2] - 30), color=color)
            img = cv2.rectangle(img, (coordinate[0], coordinate[2]), (coordinate[1], coordinate[3]), color, 2)
        cv2.imwrite(os.path.join(os.path.abspath(ocr_loss_debug_path), f'{str(data["i"]).zfill(8)}.png'), img)


FONT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'NotoSansCJK-Bold.otf')
FONT = ImageFont.truetype(FONT_PATH, 20)


def paint_chinese_opencv(im, chinese, pos, color):
    img_pil = Image.fromarray(im)
    fill_color = color  # (color[2], color[1], color[0])
    position = pos
    draw = ImageDraw.Draw(img_pil)
    draw.text(position, chinese, font=FONT, fill=fill_color)
    img = np.array(img_pil)
    return img


def ocr_task_consumer(worker_id, text_recogniser, ocr_queue, progress_queue,
                      sub_area, video_path, options, worker_results,
                      worker_stats):
    """
    消费者： 消费ocr_queue，将ocr队列中的数据取出，进行ocr识别，写入字幕文件中
    :param ocr_queue (current_frame_no当前帧帧号, frame 视频帧, dt_box检测框, rec_res识别结果)
    :param raw_subtitle_path
    :param sub_area
    :param video_path
    :param options
    """
    data = {'i': 1}
    # 丢失字幕的存储路径
    ocr_loss_debug_path = os.path.join(os.path.abspath(os.path.splitext(video_path)[0]), 'loss')

    raw_subtitles = []
    processed_frames = 0
    paddle_batches = 0
    paddle_frames = 0
    largest_batch = 0
    queue_wait_seconds = 0.0
    inference_seconds = 0.0
    filtering_seconds = 0.0
    accepted_text_lines = 0
    first_accept_reported = False
    empty_sample_warning_reported = False
    batch_supported = True
    try:
        while True:
            try:
                wait_started = time.perf_counter()
                item = ocr_queue.get(block=True)
                queue_wait_seconds += time.perf_counter() - wait_started
                if item[0] == -1:
                    return

                # Collect a small number of ready frames.  The short timeout
                # avoids delaying a sparse queue while still allowing a full
                # batch when the decoder has already prepared frames.
                items = [item]
                end_requested = False
                while len(items) < OCR_FRAME_BATCH_SIZE:
                    try:
                        next_item = ocr_queue.get(timeout=OCR_BATCH_WAIT_SECONDS)
                    except queue.Empty:
                        break
                    if next_item[0] == -1:
                        end_requested = True
                        break
                    items.append(next_item)

                # Some modes already provide cached OCR results.  Only send
                # frames without cached results through PaddleOCR.
                pending_indices = [
                    index
                    for index, (_, _, dt_box, rec_res, _) in enumerate(items)
                    if dt_box is None or rec_res is None
                ]
                pending_results = {}
                if pending_indices:
                    images = [items[index][1] for index in pending_indices]
                    paddle_batches += 1
                    paddle_frames += len(images)
                    largest_batch = max(largest_batch, len(images))
                    inference_started = time.perf_counter()
                    try:
                        if batch_supported:
                            results = text_recogniser.predict_batch(images)
                        else:
                            results = [
                                text_recogniser.predict(image)
                                for image in images
                            ]
                    except Exception as batch_error:
                        # Keep extraction usable if a PaddleOCR build rejects
                        # multi-image input or the selected batch is too large.
                        print(
                            f'VSE OCR micro-batch fallback to single frame: '
                            f'{batch_error}'
                        )
                        batch_supported = False
                        results = [
                            text_recogniser.predict(image)
                            for image in images
                        ]
                    inference_seconds += (
                        time.perf_counter() - inference_started
                    )
                    pending_results = dict(zip(pending_indices, results))

                filtering_started = time.perf_counter()
                for index, (
                    frame_no, frame, dt_box, rec_res, bypass_sub_area
                ) in enumerate(items):
                    if index in pending_results:
                        dt_box, rec_res = pending_results[index]
                    data['i'] = frame_no
                    # Exact ROI crops use a local 0..width / 0..height
                    # coordinate system.  Keep confidence filtering active,
                    # while avoiding comparison with the obsolete full-frame
                    # rectangle that caused every result to be rejected.
                    effective_sub_area = sub_area
                    if bypass_sub_area:
                        effective_sub_area = SimpleNamespace(
                            xmin=0,
                            xmax=frame.shape[1],
                            ymin=0,
                            ymax=frame.shape[0],
                        )
                    accepted_before = len(raw_subtitles)
                    extract_subtitles(
                        data, text_recogniser, frame, raw_subtitles,
                        effective_sub_area,
                        options, dt_box, rec_res, ocr_loss_debug_path
                    )
                    accepted_now = len(raw_subtitles) - accepted_before
                    if accepted_now > 0:
                        accepted_text_lines += accepted_now
                    elif getattr(options, 'TURBO_SCAN', False):
                        # Keep an explicit empty sample in the timeline.
                        # Without this marker, the subtitle generator would
                        # stretch the previous sentence across silent gaps.
                        raw_subtitles.append(
                            f'{str(frame_no).zfill(8)}\t'
                            f'(0, 0, 0, 0)\t{TURBO_BLANK_MARKER}\n'
                        )
                    if accepted_now > 0 and \
                            not first_accept_reported:
                        print(
                            f'VSE OCR worker {worker_id} accepted its first '
                            f'subtitle at frame {frame_no}',
                            flush=True
                        )
                        first_accept_reported = True
                    # Report work only after OCR and filtering have completed.
                    # This keeps the UI progress synchronized with real work
                    # instead of with frames waiting in the queue.
                    processed_frames += 1
                    progress_queue.put(frame_no)
                    if processed_frames >= 50 and \
                            accepted_text_lines == 0 and \
                            not empty_sample_warning_reported:
                        print(
                            f'VSE warning: OCR worker {worker_id} has '
                            f'processed 50 candidate frames but accepted '
                            f'no text. Check the selected rectangle and '
                            f'confidence threshold.',
                            flush=True
                        )
                        empty_sample_warning_reported = True
                filtering_seconds += (
                    time.perf_counter() - filtering_started
                )

                if end_requested:
                    return
            except Exception as e:
                print(f'VSE OCR worker {worker_id} stopped: {e}', flush=True)
                break
    finally:
        worker_results[worker_id] = raw_subtitles
        worker_stats[worker_id] = {
            'processed_frames': processed_frames,
            'paddle_batches': paddle_batches,
            'paddle_frames': paddle_frames,
            'largest_batch': largest_batch,
            'queue_wait_seconds': queue_wait_seconds,
            'inference_seconds': inference_seconds,
            'filtering_seconds': filtering_seconds,
            'accepted_text_lines': accepted_text_lines,
        }


def _unpack_ocr_task(task):
    """Normalize old six-field and new seven-field OCR tasks."""
    if len(task) >= 7:
        (total_frame_count, current_frame_no, dt_box, rec_res,
         total_ms, default_subtitle_area, rgb_image_path) = task[:7]
    else:
        (total_frame_count, current_frame_no, dt_box, rec_res,
         total_ms, default_subtitle_area) = task
        rgb_image_path = None
    return (
        total_frame_count, current_frame_no, dt_box, rec_res,
        total_ms, default_subtitle_area, rgb_image_path
    )


class ParallelVideoDecoder:
    """Give every worker its own VideoCapture for safe parallel seeking."""

    def __init__(self, video_path):
        self.video_path = video_path
        self.local = threading.local()
        self.captures = []
        self.captures_lock = threading.Lock()

    def _open_capture(self):
        cap = cv2.VideoCapture(self.video_path)
        self.local.capture = cap
        with self.captures_lock:
            self.captures.append(cap)
        return cap

    def _get_capture(self):
        cap = getattr(self.local, 'capture', None)
        if cap is None or not cap.isOpened():
            cap = self._open_capture()
        return cap

    def decode(self, current_frame_no, total_ms, default_subtitle_area,
               selected_sub_area):
        cap = self._get_capture()
        if total_ms is not None:
            cap.set(cv2.CAP_PROP_POS_MSEC, total_ms)
        else:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, current_frame_no - 1))
        ret, frame = cap.read()

        # Some codecs occasionally reject a random seek after a long run.
        # Reopening only this worker's capture gives the task one safe retry.
        if not ret:
            cap.release()
            cap = self._open_capture()
            if total_ms is not None:
                cap.set(cv2.CAP_PROP_POS_MSEC, total_ms)
            else:
                cap.set(
                    cv2.CAP_PROP_POS_FRAMES, max(0, current_frame_no - 1)
                )
            ret, frame = cap.read()
        if not ret:
            return None
        bypass_sub_area = False
        if selected_sub_area is not None:
            frame = _crop_to_selected_area(frame, selected_sub_area)
            bypass_sub_area = True
        elif default_subtitle_area is not None:
            frame = frame_preprocess(default_subtitle_area, frame)
        if frame is None or frame.size == 0:
            return None
        return frame, bypass_sub_area

    def close(self):
        with self.captures_lock:
            captures = list(self.captures)
            self.captures.clear()
        for cap in captures:
            cap.release()


def _crop_to_selected_area(frame, selected_sub_area):
    """Crop to the user's exact full-frame ROI and return local coordinates."""
    height, width = frame.shape[:2]
    xmin = max(0, min(width, int(selected_sub_area.xmin)))
    xmax = max(0, min(width, int(selected_sub_area.xmax)))
    ymin = max(0, min(height, int(selected_sub_area.ymin)))
    ymax = max(0, min(height, int(selected_sub_area.ymax)))
    if xmin >= xmax or ymin >= ymax:
        return None
    # A contiguous copy releases the full decoded frame before the image waits
    # in the OCR queue and gives Paddle an efficient, compact input buffer.
    return np.ascontiguousarray(frame[ymin:ymax, xmin:xmax])


def _put_decoded_frame(ocr_queue, task_info, frame, bypass_sub_area=False):
    (_, current_frame_no, dt_box, rec_res,
     _, _, _) = task_info
    if frame is None:
        return False
    ocr_queue.put((
        current_frame_no, frame, dt_box, rec_res, bypass_sub_area
    ))
    return True


def _produce_sequential_frames(
        first_task, ocr_queue, task_queue, video_path, selected_sub_area):
    """Keep accurate/FPS modes on the original conservative decode path."""
    cap = cv2.VideoCapture(video_path)
    decoded_count = 0
    failed_count = 0
    task_info = first_task
    try:
        while True:
            (_, current_frame_no, _, _, total_ms,
             default_subtitle_area, _) = task_info
            if current_frame_no == -1:
                break
            if total_ms is not None:
                cap.set(cv2.CAP_PROP_POS_MSEC, total_ms)
            else:
                cap.set(
                    cv2.CAP_PROP_POS_FRAMES, max(0, current_frame_no - 1)
                )
            ret, frame = cap.read()
            if ret:
                # Accurate mode uses cached full-frame boxes and must retain
                # the original coordinate system.  Exact ROI cropping is only
                # safe for timestamp tasks whose OCR is performed downstream.
                bypass_sub_area = False
                if total_ms is not None and selected_sub_area is not None:
                    frame = _crop_to_selected_area(
                        frame, selected_sub_area
                    )
                    bypass_sub_area = True
                elif selected_sub_area is None and \
                        default_subtitle_area is not None:
                    frame = frame_preprocess(default_subtitle_area, frame)
                if _put_decoded_frame(
                    ocr_queue, task_info, frame, bypass_sub_area
                ):
                    decoded_count += 1
                else:
                    failed_count += 1
            else:
                failed_count += 1
            task_info = _unpack_ocr_task(task_queue.get(block=True))
    finally:
        cap.release()
    return decoded_count, failed_count


def _produce_prefetched_frames(
        first_task, ocr_queue, task_queue, video_path, selected_sub_area):
    """Decode VSF candidate timestamps ahead while preserving task order."""
    decoder = ParallelVideoDecoder(video_path)
    pending = deque()
    decoded_count = 0
    failed_count = 0
    end_requested = False

    def submit(executor, task_info):
        (_, current_frame_no, _, _, total_ms,
         default_subtitle_area, _) = task_info
        future = executor.submit(
            decoder.decode,
            current_frame_no,
            total_ms,
            default_subtitle_area,
            selected_sub_area
        )
        pending.append((task_info, future))

    print(
        f'VSE original-video decode prefetch enabled: '
        f'{DECODE_WORKERS} workers; window {DECODE_PREFETCH}',
        flush=True
    )
    executor = ThreadPoolExecutor(
        max_workers=DECODE_WORKERS,
        thread_name_prefix='vse-decode'
    )
    try:
        submit(executor, first_task)
        while pending or not end_requested:
            # Keep a bounded window of timestamp seeks in flight.  A tiny
            # timeout lets OCR start promptly when VSF produces sparse tasks.
            while not end_requested and len(pending) < DECODE_PREFETCH:
                try:
                    task = task_queue.get(
                        timeout=DECODE_COLLECT_WAIT_SECONDS
                    )
                except queue.Empty:
                    break
                task_info = _unpack_ocr_task(task)
                if task_info[1] == -1:
                    end_requested = True
                    break
                submit(executor, task_info)

            if pending:
                task_info, future = pending.popleft()
                try:
                    decoded = future.result()
                except Exception as error:
                    print(
                        f'VSE parallel decode failed for frame '
                        f'{task_info[1]}: {error}',
                        flush=True
                    )
                    decoded = None
                if decoded is None:
                    frame, bypass_sub_area = None, False
                else:
                    frame, bypass_sub_area = decoded
                if _put_decoded_frame(
                    ocr_queue, task_info, frame, bypass_sub_area
                ):
                    decoded_count += 1
                else:
                    failed_count += 1
            elif not end_requested:
                # No task was available during the short collection window.
                # Block once instead of spinning and wasting a CPU core.
                task_info = _unpack_ocr_task(task_queue.get(block=True))
                if task_info[1] == -1:
                    end_requested = True
                else:
                    submit(executor, task_info)
    finally:
        executor.shutdown(wait=True, cancel_futures=True)
        decoder.close()
    return decoded_count, failed_count


def _produce_direct_vsf_frames(
        first_task, ocr_queue, task_queue, video_path, selected_sub_area):
    """
    Reuse VideoSubFinder candidate images instead of seeking the MP4 again.

    VideoSubFinder has already decoded and saved these files.  Reading the
    compact candidates avoids three independent random-seek decoders competing
    with VideoSubFinder for the same source video.  A failed/partial image read
    falls back to one original-video decoder for correctness.
    """
    metadata_capture = cv2.VideoCapture(video_path)
    expected_width = int(
        metadata_capture.get(cv2.CAP_PROP_FRAME_WIDTH)
    )
    expected_height = int(
        metadata_capture.get(cv2.CAP_PROP_FRAME_HEIGHT)
    )
    metadata_capture.release()

    fallback_decoder = ParallelVideoDecoder(video_path)
    direct_rgb_count = 0
    fallback_video_count = 0
    failed_count = 0
    task_wait_seconds = 0.0
    image_read_seconds = 0.0
    fallback_decode_seconds = 0.0
    first_shape_reported = False
    task_info = first_task
    try:
        while True:
            (_, current_frame_no, _, _, total_ms,
             default_subtitle_area, rgb_image_path) = task_info
            if current_frame_no == -1:
                break

            frame = None
            bypass_sub_area = False
            if rgb_image_path:
                read_started = time.perf_counter()
                for retry in range(VSF_RGB_READ_RETRIES):
                    frame = cv2.imread(
                        rgb_image_path, cv2.IMREAD_COLOR
                    )
                    if frame is not None and frame.size:
                        break
                    frame = None
                    if retry + 1 < VSF_RGB_READ_RETRIES:
                        time.sleep(VSF_RGB_RETRY_SECONDS)
                image_read_seconds += time.perf_counter() - read_started

            if frame is not None:
                direct_rgb_count += 1
                is_full_frame = (
                    expected_width > 0 and expected_height > 0
                    and frame.shape[1] == expected_width
                    and frame.shape[0] == expected_height
                )
                if is_full_frame and selected_sub_area is not None:
                    frame = _crop_to_selected_area(
                        frame, selected_sub_area
                    )
                    bypass_sub_area = True
                elif not is_full_frame:
                    # VideoSubFinder normally saves the selected crop.  OCR
                    # boxes are therefore local to this candidate image.
                    bypass_sub_area = True
                elif default_subtitle_area is not None:
                    frame = frame_preprocess(
                        default_subtitle_area, frame
                    )

                if not first_shape_reported:
                    shape = (
                        'invalid' if frame is None
                        else f'{frame.shape[1]}x{frame.shape[0]}'
                    )
                    print(
                        'VSE direct VSF candidate input enabled: '
                        f'first OCR image={shape}; '
                        f'original={expected_width}x{expected_height}',
                        flush=True
                    )
                    first_shape_reported = True

            if frame is None:
                fallback_started = time.perf_counter()
                decoded = fallback_decoder.decode(
                    current_frame_no,
                    total_ms,
                    default_subtitle_area,
                    selected_sub_area
                )
                fallback_decode_seconds += (
                    time.perf_counter() - fallback_started
                )
                if decoded is None:
                    failed_count += 1
                else:
                    frame, bypass_sub_area = decoded
                    fallback_video_count += 1

            if frame is not None:
                _put_decoded_frame(
                    ocr_queue, task_info, frame, bypass_sub_area
                )

            wait_started = time.perf_counter()
            task_info = _unpack_ocr_task(
                task_queue.get(block=True)
            )
            task_wait_seconds += time.perf_counter() - wait_started
    finally:
        fallback_decoder.close()

    return {
        'decoded_frames': direct_rgb_count + fallback_video_count,
        'direct_rgb_frames': direct_rgb_count,
        'fallback_video_frames': fallback_video_count,
        'failed_frames': failed_count,
        'decode_workers': 0,
        'prefetch': 0,
        'task_wait_seconds': task_wait_seconds,
        'image_read_seconds': image_read_seconds,
        'fallback_decode_seconds': fallback_decode_seconds,
    }


def _produce_nvdec_sampled_frames(
        first_task, ocr_queue, progress_queue, video_path,
        selected_sub_area, turbo_config):
    """
    Stream fixed-rate, exact-ROI BGR frames from FFmpeg NVDEC into OCR.

    The fps filter runs while frames are still CUDA surfaces, so only the
    requested samples are downloaded to system memory.  FFmpeg then crops the
    selected subtitle strip and writes fixed-size bgr24 frames to stdout.
    """
    if selected_sub_area is None:
        raise RuntimeError('NVDEC requires an explicitly selected subtitle area')

    ffmpeg_path = _resolve_ffmpeg_path()
    codec, decoder = _probe_cuvid_decoder(ffmpeg_path, video_path)
    sample_fps = max(
        0.5, min(10.0, float(turbo_config.get('sample_fps', 2.0)))
    )

    metadata = cv2.VideoCapture(video_path)
    source_fps = metadata.get(cv2.CAP_PROP_FPS)
    total_frames = int(metadata.get(cv2.CAP_PROP_FRAME_COUNT))
    source_width = int(metadata.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(metadata.get(cv2.CAP_PROP_FRAME_HEIGHT))
    metadata.release()
    if source_fps <= 0:
        source_fps = 30.0
    if total_frames <= 0:
        total_frames = int(first_task[0])

    # NV12 is 4:2:0, so crop boundaries must be chroma-aligned.  Expand an
    # odd user selection by at most one pixel instead of letting FFmpeg round
    # it internally, which would make the raw pipe frame size ambiguous.
    crop_x = max(0, min(source_width - 2, int(selected_sub_area.xmin)))
    crop_y = max(0, min(source_height - 2, int(selected_sub_area.ymin)))
    crop_x -= crop_x % 2
    crop_y -= crop_y % 2
    crop_x2 = max(
        crop_x + 2,
        min(source_width, int(selected_sub_area.xmax) + 1)
    )
    crop_y2 = max(
        crop_y + 2,
        min(source_height, int(selected_sub_area.ymax) + 1)
    )
    crop_x2 -= crop_x2 % 2
    crop_y2 -= crop_y2 % 2
    crop_width = crop_x2 - crop_x
    crop_height = crop_y2 - crop_y
    if crop_width <= 0 or crop_height <= 0:
        raise RuntimeError(
            f'invalid NVDEC crop: {crop_width}x{crop_height}+{crop_x}+{crop_y}'
        )

    video_filter = (
        f'fps={sample_fps:.8f},'
        f'hwdownload,format=nv12,'
        f'crop={crop_width}:{crop_height}:{crop_x}:{crop_y},'
        f'format=bgr24'
    )
    command = [
        ffmpeg_path,
        '-hide_banner',
        '-loglevel', 'error',
        '-nostdin',
        '-hwaccel', 'cuda',
        '-hwaccel_output_format', 'cuda',
        '-c:v', decoder,
        '-i', video_path,
        '-an',
        '-sn',
        '-dn',
        '-vf', video_filter,
        '-pix_fmt', 'bgr24',
        '-f', 'rawvideo',
        'pipe:1',
    ]

    stderr_tail = deque(maxlen=20)

    def collect_stderr(process):
        for raw_line in iter(process.stderr.readline, b''):
            line = raw_line.decode('utf-8', errors='replace').strip()
            if line:
                stderr_tail.append(line)

    scan_started = time.perf_counter()
    process = subprocess.Popen(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=max(1024 * 1024, crop_width * crop_height * 3 * 2),
        creationflags=_subprocess_creation_flags(),
    )
    stderr_thread = Thread(
        target=collect_stderr, args=(process,), daemon=True
    )
    stderr_thread.start()

    sampled_frames = 0
    failed_frames = 0
    queue_backpressure_seconds = 0.0
    frame_size = crop_width * crop_height * 3
    report_interval = max(1, int(round(sample_fps * 10)))

    print(
        'VSE FFmpeg NVDEC scanner started: '
        f'codec={codec}; decoder={decoder}; '
        f'source={source_fps:.3f} fps; '
        f'sample rate={sample_fps:.3f}/s; '
        f'crop={crop_width}x{crop_height}+{crop_x}+{crop_y}; '
        f'ffmpeg={ffmpeg_path}',
        flush=True
    )
    try:
        while True:
            raw_frame = _read_exact_bytes(process.stdout, frame_size)
            if not raw_frame:
                break

            frame_no = min(
                max(0, total_frames - 1),
                int(round(sampled_frames * source_fps / sample_fps))
            )
            frame = np.frombuffer(
                raw_frame, dtype=np.uint8
            ).reshape((crop_height, crop_width, 3)).copy()
            sampled_task = (
                first_task[0],
                frame_no,
                None,
                None,
                None,
                first_task[5],
                None,
            )
            put_started = time.perf_counter()
            _put_decoded_frame(
                ocr_queue, sampled_task, frame, True
            )
            queue_backpressure_seconds += (
                time.perf_counter() - put_started
            )
            sampled_frames += 1

            if sampled_frames % report_interval == 0:
                progress_queue.put((
                    'scan_progress',
                    {'frame_no': frame_no}
                ))

        return_code = process.wait()
        stderr_thread.join(timeout=2)
        if return_code != 0:
            error_text = ' | '.join(stderr_tail) or \
                f'FFmpeg exited with code {return_code}'
            error = RuntimeError(error_text)
            error.sampled_frames = sampled_frames
            raise error
        if sampled_frames == 0:
            error = RuntimeError('FFmpeg NVDEC produced zero frames')
            error.sampled_frames = 0
            raise error
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
        if process.stdout:
            process.stdout.close()
        if process.stderr:
            process.stderr.close()

    progress_queue.put((
        'scan_progress',
        {'frame_no': total_frames}
    ))
    return {
        'engine': 'nvdec',
        'decoded_frames': sampled_frames,
        'direct_rgb_frames': 0,
        'fallback_video_frames': 0,
        'failed_frames': failed_frames,
        'decode_workers': 1,
        'prefetch': 0,
        'task_wait_seconds': 0.0,
        'image_read_seconds': 0.0,
        'fallback_decode_seconds': 0.0,
        'scanned_frames': total_frames,
        'sampled_frames': sampled_frames,
        'sample_fps': sample_fps,
        'sample_interval': source_fps / sample_fps,
        'scan_seconds': time.perf_counter() - scan_started,
        'queue_backpressure_seconds': queue_backpressure_seconds,
        'ffmpeg_path': ffmpeg_path,
        'decoder': decoder,
        'crop': f'{crop_width}x{crop_height}+{crop_x}+{crop_y}',
    }


def _produce_turbo_sampled_frames(
        first_task, ocr_queue, progress_queue, video_path,
        selected_sub_area, turbo_config):
    """
    Decode the source once and submit only fixed-rate subtitle-area samples.

    cap.grab() advances unsampled frames without converting each one to BGR.
    cap.retrieve() is called only for OCR samples.  The cropped arrays remain
    inside this child process and feed the consumer threads directly, avoiding
    multiprocessing copies and temporary JPEG files.
    """
    sample_fps = max(
        0.5, min(10.0, float(turbo_config.get('sample_fps', 2.0)))
    )
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f'cannot open video for turbo scan: {video_path}')

    source_fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    if source_fps <= 0:
        source_fps = 30.0
    sample_interval = max(1, int(round(source_fps / sample_fps)))
    actual_sample_fps = source_fps / sample_interval

    scanned_frames = 0
    sampled_frames = 0
    failed_frames = 0
    queue_backpressure_seconds = 0.0
    report_interval = max(250, int(round(source_fps * 10)))
    scan_started = time.perf_counter()

    print(
        'VSE turbo sequential scanner started: '
        f'source={source_fps:.3f} fps; '
        f'every {sample_interval} frames; '
        f'actual sample rate={actual_sample_fps:.3f}/s',
        flush=True
    )
    try:
        frame_no = 0
        while cap.grab():
            if frame_no % sample_interval == 0:
                ret, frame = cap.retrieve()
                if not ret or frame is None or frame.size == 0:
                    failed_frames += 1
                else:
                    bypass_sub_area = False
                    if selected_sub_area is not None:
                        frame = _crop_to_selected_area(
                            frame, selected_sub_area
                        )
                        bypass_sub_area = True
                    else:
                        default_subtitle_area = first_task[5]
                        if default_subtitle_area is not None:
                            frame = frame_preprocess(
                                default_subtitle_area, frame
                            )
                    if frame is None or frame.size == 0:
                        failed_frames += 1
                    else:
                        sampled_task = (
                            first_task[0],
                            frame_no,
                            None,
                            None,
                            None,
                            first_task[5],
                            None,
                        )
                        put_started = time.perf_counter()
                        _put_decoded_frame(
                            ocr_queue, sampled_task, frame,
                            bypass_sub_area
                        )
                        queue_backpressure_seconds += (
                            time.perf_counter() - put_started
                        )
                        sampled_frames += 1

            scanned_frames = frame_no + 1
            if scanned_frames % report_interval == 0:
                progress_queue.put((
                    'scan_progress',
                    {'frame_no': scanned_frames}
                ))
            frame_no += 1
    finally:
        cap.release()

    progress_queue.put((
        'scan_progress',
        {'frame_no': max(total_frames, scanned_frames)}
    ))
    return {
        'engine': 'turbo',
        'decoded_frames': sampled_frames,
        'direct_rgb_frames': 0,
        'fallback_video_frames': 0,
        'failed_frames': failed_frames,
        'decode_workers': 1,
        'prefetch': 0,
        'task_wait_seconds': 0.0,
        'image_read_seconds': 0.0,
        'fallback_decode_seconds': 0.0,
        'scanned_frames': scanned_frames,
        'sampled_frames': sampled_frames,
        'sample_fps': actual_sample_fps,
        'sample_interval': sample_interval,
        'scan_seconds': time.perf_counter() - scan_started,
        'queue_backpressure_seconds': queue_backpressure_seconds,
    }


def ocr_task_producer(
        ocr_queue, task_queue, progress_queue, video_path, raw_subtitle_path,
        worker_count, selected_sub_area):
    """
    生产者：自动/快速模式并行预取原视频帧，精准模式保持单路读取。
    """
    decoded_count = 0
    failed_count = 0
    decode_workers = 1
    summary = None
    try:
        first_task = _unpack_ocr_task(task_queue.get(block=True))
        if first_task[1] != -1:
            candidate_config = (
                first_task[6] if isinstance(first_task[6], dict)
                and first_task[6].get('engine') in ('turbo', 'nvdec')
                else None
            )
            use_direct_vsf = (
                USE_VSF_RGB_IMAGES
                and isinstance(first_task[6], (str, bytes, os.PathLike))
            )
            # VideoSubFinder supplies a millisecond timestamp in auto/fast
            # modes. Accurate/FPS paths use None and stay sequential.
            use_prefetch = (
                first_task[4] is not None and DECODE_WORKERS > 1
            )
            if candidate_config is not None:
                if candidate_config.get('engine') == 'nvdec':
                    try:
                        summary = _produce_nvdec_sampled_frames(
                            first_task, ocr_queue, progress_queue, video_path,
                            selected_sub_area, candidate_config
                        )
                    except Exception as nvdec_error:
                        if getattr(nvdec_error, 'sampled_frames', 0) > 0:
                            raise
                        print(
                            'VSE NVDEC unavailable; falling back to the '
                            f'v7 OpenCV sampler: {nvdec_error}',
                            flush=True
                        )
                        summary = _produce_turbo_sampled_frames(
                            first_task, ocr_queue, progress_queue, video_path,
                            selected_sub_area, candidate_config
                        )
                        summary['engine'] = 'nvdec-opencv-fallback'
                        summary['fallback_reason'] = str(nvdec_error)
                else:
                    summary = _produce_turbo_sampled_frames(
                        first_task, ocr_queue, progress_queue, video_path,
                        selected_sub_area, candidate_config
                    )
            elif use_direct_vsf:
                summary = _produce_direct_vsf_frames(
                    first_task, ocr_queue, task_queue, video_path,
                    selected_sub_area
                )
            elif use_prefetch:
                decode_workers = DECODE_WORKERS
                if selected_sub_area is not None:
                    print(
                        'VSE exact selected-area OCR crop enabled: '
                        f'x={selected_sub_area.xmin}:'
                        f'{selected_sub_area.xmax}; '
                        f'y={selected_sub_area.ymin}:'
                        f'{selected_sub_area.ymax}',
                        flush=True
                    )
                decoded_count, failed_count = _produce_prefetched_frames(
                    first_task, ocr_queue, task_queue, video_path,
                    selected_sub_area
                )
            else:
                decoded_count, failed_count = _produce_sequential_frames(
                    first_task, ocr_queue, task_queue, video_path,
                    selected_sub_area
                )
    except Exception as error:
        print(f'VSE frame producer stopped: {error}', flush=True)
    finally:
        if summary is None:
            summary = {
                'decoded_frames': decoded_count,
                'direct_rgb_frames': 0,
                'fallback_video_frames': decoded_count,
                'failed_frames': failed_count,
                'decode_workers': decode_workers,
                'prefetch': (
                    DECODE_PREFETCH if decode_workers > 1 else 0
                ),
                'task_wait_seconds': 0.0,
                'image_read_seconds': 0.0,
                'fallback_decode_seconds': 0.0,
            }
        progress_queue.put(('input_summary', summary))
        # Every consumer needs its own sentinel.  A single sentinel would
        # leave the second engine blocked forever after the producer ends.
        for _ in range(worker_count):
            ocr_queue.put((-1, None, None, None, False))


def _write_merged_subtitles(raw_subtitle_path, worker_results):
    """Merge independent consumer results into one chronological raw file."""
    merged = []
    for lines in worker_results:
        if lines:
            merged.extend(lines)
    merged.sort(key=lambda value: int(value.split('\t', 1)[0]))
    with open(raw_subtitle_path, mode='w+', encoding='utf-8') as output_file:
        output_file.writelines(merged)
    return sum(TURBO_BLANK_MARKER not in line for line in merged)


def subtitle_extract_handler(task_queue, progress_queue, video_path, raw_subtitle_path, sub_area, options):
    """
    创建并开启一个视频帧提取线程与一个ocr识别线程
    :param task_queue 任务队列，(total_frame_count总帧数, current_frame_no当前帧, dt_box检测框, rec_res识别结果, subtitle_area字幕区域)
    :param progress_queue 进度队列
    :param video_path 视频路径
    :param raw_subtitle_path 原始字幕文件路径
    :param sub_area 字幕区域
    :param options 选项
    """
    handler_started = time.perf_counter()
    # 删除缓存
    if os.path.exists(raw_subtitle_path):
        os.remove(raw_subtitle_path)
    # Delete OCR-loss cache once.  Multiple consumers must not race while
    # removing the same directory.
    ocr_loss_debug_path = os.path.join(
        os.path.abspath(os.path.splitext(video_path)[0]), 'loss'
    )
    if os.path.exists(ocr_loss_debug_path):
        shutil.rmtree(ocr_loss_debug_path, True)

    # Create and warm up predictors serially.  If the current GPU cannot hold
    # a second copy, keep the already-tested first engine and continue safely.
    # Accurate mode already owns a GPU detector/OCR pipeline in the parent
    # process.  Loading two more server-model copies would risk exhausting an
    # 8 GB card.  Auto/fast are the modes where two child predictors can fill
    # each other's preprocessing and postprocessing gaps.
    requested_workers = (
        1 if getattr(options, 'MODE', None) == 'accurate' else OCR_WORKERS
    )
    recognisers = []
    model_init_started = time.perf_counter()
    for worker_id in range(requested_workers):
        recogniser = OcrRecogniser()
        recogniser.hardware_accelerator = options.HARDWARD_ACCELERATOR
        try:
            recogniser.prepare()
            recognisers.append(recogniser)
        except Exception as error:
            recogniser.release()
            if recognisers:
                print(
                    f'VSE second OCR engine unavailable; fallback to '
                    f'{len(recognisers)} engine: {error}',
                    flush=True
                )
                break
            raise
    model_init_seconds = time.perf_counter() - model_init_started

    worker_count = len(recognisers)
    print(
        f'VSE OCR engines ready: {worker_count}; '
        f'micro-batch={OCR_FRAME_BATCH_SIZE} frames per engine',
        flush=True
    )

    # Keep enough decoded frames ready for both independent engines without
    # allowing unbounded host-memory growth.
    ocr_queue = queue.Queue(
        max(24, min(128, OCR_FRAME_BATCH_SIZE * worker_count * 3))
    )
    worker_results = [None] * worker_count
    worker_stats = [None] * worker_count

    ocr_event_producer_thread = Thread(target=ocr_task_producer,
                                       args=(ocr_queue, task_queue, progress_queue, video_path, raw_subtitle_path, worker_count, sub_area,),
                                       daemon=True)
    ocr_event_consumer_threads = [
        Thread(
            target=ocr_task_consumer,
            args=(
                worker_id, recogniser, ocr_queue, progress_queue, sub_area,
                video_path, options, worker_results, worker_stats
            ),
            daemon=True
        )
        for worker_id, recogniser in enumerate(recognisers)
    ]

    for consumer_thread in ocr_event_consumer_threads:
        consumer_thread.start()
    ocr_event_producer_thread.start()
    ocr_event_producer_thread.join()
    for consumer_thread in ocr_event_consumer_threads:
        consumer_thread.join()
    # Release GPU predictors only after both workers have stopped.  Calling
    # CUDA empty_cache from one worker while its peer is still inferring can
    # introduce an avoidable cross-thread allocator race.
    for recogniser in recognisers:
        recogniser.release()

    accepted_lines = _write_merged_subtitles(
        raw_subtitle_path, worker_results
    )
    print(
        f'VSE OCR accepted subtitle lines: {accepted_lines}',
        flush=True
    )
    if accepted_lines == 0:
        print(
            'VSE warning: OCR finished but accepted zero subtitle lines. '
            'Check the selected rectangle and lower the confidence threshold.',
            flush=True
        )
    valid_stats = [stats for stats in worker_stats if stats is not None]
    processed_frames = sum(
        stats['processed_frames'] for stats in valid_stats
    )
    paddle_frames = sum(stats['paddle_frames'] for stats in valid_stats)
    paddle_batches = sum(stats['paddle_batches'] for stats in valid_stats)
    largest_batch = max(
        (stats['largest_batch'] for stats in valid_stats),
        default=0
    )
    queue_wait_seconds = sum(
        stats['queue_wait_seconds'] for stats in valid_stats
    )
    inference_seconds = sum(
        stats['inference_seconds'] for stats in valid_stats
    )
    filtering_seconds = sum(
        stats['filtering_seconds'] for stats in valid_stats
    )
    progress_queue.put(('ocr_summary', {
        'processed_frames': processed_frames,
        'average_batch': paddle_frames / max(1, paddle_batches),
        'largest_batch': largest_batch,
        'workers': worker_count,
        'accepted_lines': accepted_lines,
        'model_init_seconds': model_init_seconds,
        'queue_wait_seconds': queue_wait_seconds,
        'inference_seconds': inference_seconds,
        'filtering_seconds': filtering_seconds,
        'handler_seconds': time.perf_counter() - handler_started,
    }))
    progress_queue.put(-1)


def async_start(video_path, raw_subtitle_path, sub_area, options):
    """
    开始进程处理异步任务
    options.REC_CHAR_TYPE
    options.DROP_SCORE
    options.SUB_AREA_DEVIATION_RATE
    options.DEBUG_OCR_LOSS
    options.HARDWARD_ACCELERATOR
    """
    assert 'REC_CHAR_TYPE' in options, "options缺少参数：REC_CHAR_TYPE"
    assert 'DROP_SCORE' in options, "options缺少参数: DROP_SCORE'"
    assert 'SUB_AREA_DEVIATION_RATE' in options, "options缺少参数: SUB_AREA_DEVIATION_RATE"
    assert 'DEBUG_OCR_LOSS' in options, "options缺少参数: DEBUG_OCR_LOSS"
    assert 'HARDWARD_ACCELERATOR' in options, "options缺少参数: HARDWARD_ACCELERATOR"
    # MODE is optional for compatibility with older callers.  Missing MODE
    # follows the auto/fast dual-engine path.
    # 创建一个任务队列
    # 任务格式为：(total_frame_count总帧数, current_frame_no当前帧, dt_box检测框, rec_res识别结果, subtitle_area字幕区域)
    task_queue = Queue()
    # 创建一个进度更新队列
    progress_queue = Queue()
    # 新建一个进程
    p = Process(target=subtitle_extract_handler,
                args=(task_queue, progress_queue, video_path, raw_subtitle_path, sub_area, SimpleNamespace(**options),))
    # 启动进程
    p.start()
    return p, task_queue, progress_queue


def frame_preprocess(subtitle_area, frame):
    """
    将视频帧进行裁剪
    """
    # 对于分辨率大于1920*1080的视频，将其视频帧进行等比缩放至1280*720进行识别
    # paddlepaddle会将图像压缩为640*640
    # if self.frame_width > 1280:
    #     scale_rate = round(float(1280 / self.frame_width), 2)
    #     frames = cv2.resize(frames, None, fx=scale_rate, fy=scale_rate, interpolation=cv2.INTER_AREA)
    # 如果字幕出现的区域在下部分
    if subtitle_area == SubtitleArea.LOWER_PART:
        cropped = int(frame.shape[0] // 2)
        # 将视频帧切割为下半部分
        frame = frame[cropped:]
    # 如果字幕出现的区域在上半部分
    elif subtitle_area == SubtitleArea.UPPER_PART:
        cropped = int(frame.shape[0] // 2)
        # 将视频帧切割为下半部分
        frame = frame[:cropped]
    return frame


if __name__ == "__main__":
    pass
