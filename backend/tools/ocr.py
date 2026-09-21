import os
import gc
import numpy as np
from backend.config import *
import importlib
from paddleocr import PaddleOCR
from backend.tools.hardware_accelerator import HardwareAccelerator
from backend.tools.paddle_model_config import PaddleModelConfig


def _env_enabled(name, default='0'):
    return os.environ.get(name, default).strip().lower() not in (
        '0', 'false', 'no', 'off'
    )


def resolve_ocr_model_selection(model_config):
    """
    Return the effective DET/REC paths and model names for this run.

    Auto mode normally uses both PP-OCRv5 server models.  On a manually
    selected, very short subtitle ROI the server detector is unnecessarily
    expensive.  VSE_AUTO_MOBILE_DET keeps the higher-quality server
    recogniser while substituting only the mobile detector.  Fast and accurate
    modes remain untouched, and a missing mobile model safely falls back to
    VSE's original selection.
    """
    det_path = model_config.DET_MODEL_PATH
    rec_path = model_config.REC_MODEL_PATH
    det_name = model_config.DET_MODEL_NAME
    rec_name = model_config.REC_MODEL_NAME
    hybrid = False

    # The high-speed auto profile is on by default.  Windows Explorer and an
    # already-open PowerShell can retain a stale environment block after the
    # installer writes a user variable, so requiring an inherited "1" made the
    # profile silently stay off.  An explicit "0" still disables it.
    if config.mode.value == 'auto' and _env_enabled(
            'VSE_AUTO_MOBILE_DET', '1'):
        mobile_det_path = os.path.join(
            os.path.dirname(det_path),
            'PP-OCRv5_mobile_det_infer',
        )
        if os.path.isdir(mobile_det_path):
            det_path = mobile_det_path
            det_name = 'PP-OCRv5_mobile_det'
            hybrid = True

    return det_path, rec_path, det_name, rec_name, hybrid


# 加载文本检测+识别模型
class OcrRecogniser:
    def __init__(self):
        self.recogniser = None
        # Reuse the initialized singleton.  Creating HardwareAccelerator()
        # directly leaves __cuda=False until initialize() is called; accurate
        # mode constructs OcrRecogniser in the main process and therefore used
        # to fall back silently to CPU.
        self.hardware_accelerator = HardwareAccelerator.instance()

    @staticmethod
    def y_round(y):
        y_min = y + 10 - y % 10
        y_max = y - y % 10
        if abs(y - y_min) < abs(y - y_max):
            return y_min
        else:
            return y_max

    def predict(self, image):
        return self.predict_batch([image])[0]

    def prepare(self):
        """
        Build and warm up this predictor before its consumer thread starts.

        Initialising the two predictors serially avoids Paddle model creation
        races.  The warm-up also makes a second-engine VRAM failure happen here,
        where the caller can safely fall back to one engine without losing an
        OCR task.
        """
        if not self.recogniser:
            self.recogniser = self.init_model()
        self.predict(np.zeros((320, 640, 3), dtype=np.uint8))
        return self

    def release(self):
        """Release a partially-created predictor during safe fallback."""
        self.recogniser = None
        gc.collect()
        try:
            import paddle
            if paddle.device.is_compiled_with_cuda():
                paddle.device.cuda.empty_cache()
        except Exception:
            pass

    def predict_batch(self, images):
        """
        Run one PaddleOCR pipeline call for several video frames.

        PaddleOCR 3.x accepts a list of numpy arrays.  Keeping the frames in a
        small micro-batch reduces Python/pipeline launch overhead and gives the
        GPU more continuous work.  One converted result is returned per input
        frame, in the same order.
        """
        if not images:
            return []

        if not self.recogniser:
            self.recogniser = self.init_model()

        results = list(self.recogniser.predict_iter(images))
        if len(results) != len(images):
            raise RuntimeError(
                f'PaddleOCR batch result count mismatch: '
                f'{len(images)} inputs, {len(results)} outputs'
            )
        return [self._convert_result(res) for res in results]

    def _convert_result(self, res):
        """Convert one PaddleOCR 3.x result to VSE's legacy result format."""
        if res is None:
            return [], []

        dt_polys = res.get('dt_polys', [])
        rec_texts = res.get('rec_texts', [])
        rec_scores = res.get('rec_scores', [])

        if len(dt_polys) == 0:
            return [], []

        # 将 dt_polys (numpy array, shape (N, points, 2)) 转换为旧的 dt_box 格式
        # 旧格式: [[(x1,y1),(x2,y2),(x3,y3),(x4,y4)], ...]
        dt_box = []
        coordinate_list = []
        for poly in dt_polys:
            points = [(int(p[0]), int(p[1])) for p in poly]
            # 取 AABB 用于排序
            xs = [p[0] for p in points]
            ys = [p[1] for p in points]
            xmin, xmax = min(xs), max(xs)
            ymin, ymax = min(ys), max(ys)
            coordinate_list.append([xmin, xmax, ymin, ymax])
            dt_box.append(points)

        # 将 rec_texts + rec_scores 转换为旧的 rec_res 格式
        rec_res = [(text, float(score)) for text, score in zip(rec_texts, rec_scores)]

        # 计算有多少行字幕，将每行字幕最小的ymin值放入lines
        lines = []
        for i in coordinate_list:
            rounded_y = self.y_round(i[2])
            if not any(abs(rounded_y - line_y) <= 10 for line_y in lines):
                lines.append(rounded_y)
        lines = sorted(lines)

        for i in coordinate_list:
            for j in lines:
                if abs(j - self.y_round(i[2])) <= 10:
                    i[2] = j

        to_rank_res = list(zip(coordinate_list, rec_res, dt_box))
        # 用sorted替代冒泡排序：先按ymin，再按xmin
        ranked_res = sorted(to_rank_res, key=lambda x: (x[0][2], x[0][0]))
        # 重建 dt_box 和 rec_res（排序后）
        sorted_dt_box = []
        sorted_rec_res = []
        for coord, rec, box in ranked_res:
            # 将 coordinate 转换回 4 点格式
            xmin, xmax, ymin, ymax = coord
            sorted_dt_box.append([(xmin, ymin), (xmax, ymin), (xmax, ymax), (xmin, ymax)])
            sorted_rec_res.append(rec)

        return sorted_dt_box, sorted_rec_res

    def init_model(self):
        model_config = PaddleModelConfig(self.hardware_accelerator)
        (
            det_model_path,
            rec_model_path,
            det_model_name,
            rec_model_name,
            hybrid,
        ) = resolve_ocr_model_selection(model_config)

        # PaddleOCR 3.x 使用 device 参数替代 use_gpu
        if self.hardware_accelerator.has_cuda():
            device = 'gpu:0'
        else:
            device = 'cpu'

        kwargs = dict(
            text_detection_model_dir=det_model_path,
            text_recognition_model_dir=rec_model_path,
            # PaddleOCR 3.x renamed rec_batch_num to
            # text_recognition_batch_size. Wire the GUI setting to the real
            # inference pipeline instead of silently using the default of 1.
            text_recognition_batch_size=config.recBatchNumber.value,
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            text_rec_score_thresh=0,
            device=device,
            # Paddle 3.3.x has a known PIR-to-oneDNN conversion bug.  oneDNN
            # is a CPU backend and is unnecessary when this pipeline runs on
            # the RTX GPU, so disable it explicitly for every OCR instance.
            enable_mkldnn=False,
        )
        if det_model_name:
            kwargs['text_detection_model_name'] = det_model_name
        if rec_model_name:
            kwargs['text_recognition_model_name'] = rec_model_name

        print(
            f"PaddleOCR text recognition batch size: "
            f"{config.recBatchNumber.value}; device: {device}; oneDNN disabled"
        )
        if config.mode.value == 'auto':
            if hybrid:
                print(
                    'VSE high-speed auto profile enabled: '
                    'DET=PP-OCRv5_mobile_det; '
                    'REC=PP-OCRv5_server_rec'
                )
            elif _env_enabled('VSE_AUTO_MOBILE_DET', '1'):
                print(
                    'VSE high-speed auto profile unavailable; '
                    'mobile detector directory was not found; '
                    'using original server detector'
                )
        return PaddleOCR(**kwargs)


def get_coordinates(dt_box):
    """
    从返回的检测框中获取坐标
    :param dt_box 检测框返回结果
    :return list 坐标点列表
    """
    coordinate_list = list()
    if isinstance(dt_box, list):
        for i in dt_box:
            i = list(i)
            (x1, y1) = int(i[0][0]), int(i[0][1])
            (x2, y2) = int(i[1][0]), int(i[1][1])
            (x3, y3) = int(i[2][0]), int(i[2][1])
            (x4, y4) = int(i[3][0]), int(i[3][1])
            xmin = max(x1, x4)
            xmax = min(x2, x3)
            ymin = max(y1, y2)
            ymax = min(y3, y4)
            coordinate_list.append((xmin, xmax, ymin, ymax))
    return coordinate_list
