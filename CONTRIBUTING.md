# Contributing

感谢你帮助验证或改进字幕工坊。

提交问题时请附上：

- GPU 完整型号与显存；
- NVIDIA 驱动版本与 Compute Capability；
- PaddlePaddle、PaddleOCR 和 FFmpeg 版本；
- 视频编码格式、分辨率与帧率；
- `diagnose.ps1` 结果；
- `VSE NVDEC scan summary`、`VSE OCR summary` 和总耗时。

不要上传无权公开的视频、字幕、个人目录、Cookie 或其他敏感信息。

代码提交至少需要通过：

```powershell
python -m py_compile backend\main.py backend\tools\ocr.py backend\tools\subtitle_ocr.py backend\tools\subtitle_detect.py backend\tools\nvidia_video.py
python -m unittest discover -s tests -p "test_*.py"
```

性能改动请同时提供相同视频、相同字幕区域、相同采样率下的前后对比。不要仅凭
GPU 占用率判断优化是否有效；还要核对最终 SRT 数量、片头、中段和片尾完整度。
