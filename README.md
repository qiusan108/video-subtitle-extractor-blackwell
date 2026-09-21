简体中文 | [English](README_en.md)

# Video Subtitle Extractor — Blackwell Edition

这是 [YaoFANGUK/video-subtitle-extractor](https://github.com/YaoFANGUK/video-subtitle-extractor) 2.2.0 的 NVIDIA Blackwell / RTX 50 系列优化分支。

它仍然做同一件事：从视频中的硬字幕提取文字并生成 SRT/TXT；这个分支主要重做了 Windows + NVIDIA GPU 的自动模式链路，让 RTX 50 系列能够使用 CUDA/NVDEC、固定频率采样和 OCR 微批处理，而不是退回较慢的旧路径。

<p align="center"><img src="design/demo.png" alt="VSE screenshot"/></p>

## 这个分支改了什么

- RTX 50 系列按实际 CUDA、FFmpeg 和视频编码能力检测，不把逻辑写死到某一个显卡型号。
- 自动模式使用 **FFmpeg NVDEC + 2 fps 固定采样 + 字幕区域裁剪 + PP-OCRv5 mobile 检测 + server 识别**。
- 默认使用 2 个 OCR 引擎、每引擎 Batch 8；第二个引擎显存不足时会自动回退到单引擎。
- NVDEC 对当前编码不可用时会回退到 OpenCV 采样路径。
- 支持 H.264、HEVC、AV1、VP8/VP9、MPEG、VC-1、MJPEG 等可由本机 FFmpeg/CUVID 提供的解码器。
- 保留上游 8 种界面语言和 87 种字幕识别语言。
- 提供一键环境配置、诊断脚本、纯逻辑测试和 GitHub Actions。

## 已验证范围

当前真机验证基线：

- GeForce RTX 5060 8 GB
- Compute Capability 12.0
- PaddlePaddle GPU 3.3.1
- CUDA 12.9
- PaddleOCR 3.4.1
- FFmpeg 8.1.2
- Windows 10/11 x64

参考视频历史最佳实测从 **1237.78 秒降到 180.66 秒**，约 **6.85×**。整理后的完整端到端回归为 **225.76 秒**，约 **5.48×**。

最新两小时 H.264 回归：

```text
源视频：215,916 帧 / 29.97 fps
采样：14,409 帧 / 2.00 fps
失败帧：0
OCR 引擎：2
平均微批次：8.0
最终 SRT：672 条字幕轴
总耗时：225.76 秒
```

这些数字只代表这套测试环境。不同显卡、视频编码、分辨率、字幕区域和后台负载都会影响结果。RTX 5050、5060 Ti、5070 / 5070 Ti、5080、5090 及 Laptop GPU 采用同一套能力检测路径，但目前不能把它们都写成“已真机验证”。

## 安装

需要：

- Windows 10/11 x64
- Python 3.12 x64
- 较新的 NVIDIA 驱动
- 含 CUDA/CUVID 解码器的 FFmpeg

创建或更新环境：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-blackwell.ps1
```

源码仓库不重复存放上游的大型模型、原生组件和测试视频。首次执行安装脚本时，会按固定的上游 2.2.0 提交下载 Windows 所需运行资源（约 445 MB），并逐个校验 SHA-256；后续运行会复用已验证文件。如只需补齐资源，也可以单独执行 `download-assets.ps1`。

安装后运行诊断：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\diagnose.ps1
```

启动：

```powershell
.\open.bat
```

FFmpeg 可以位于 PATH、项目内或相邻的 `ffmpeg` 目录，也可以通过 `VSE_FFMPEG_PATH` 指定。

## 推荐起点

- 模式：自动
- 手动框选准确、尽量窄的字幕区域
- 置信度：70
- 采样：2 fps
- OCR 引擎：2
- Batch：8

如果要对比 server 检测器，可在当前命令行会话中运行：

```powershell
set VSE_AUTO_MOBILE_DET=0
.\open.bat
```

## 质量与兼容性边界

自动模式为了速度使用 mobile 检测器定位文字。极细、模糊、低对比度字幕理论上更容易漏检，因此重要视频仍建议抽查片头、中段和片尾。

更高端显卡也不建议盲目增大 Batch；RTX 5060 的实测中 Batch 16 没有带来收益。

## 隐私

视频解码、OCR 和字幕生成都在本机完成。诊断结果可能包含 GPU 型号、驱动版本、FFmpeg 路径和依赖版本；提交 Issue 前请检查其中是否包含个人目录名。

## 开源与上游

本项目是上游 VSE 的修改版，继续遵循 Apache License 2.0，并保留上游版权与归属信息。

- 变更记录：[CHANGELOG.md](CHANGELOG.md)
- 贡献说明：[CONTRIBUTING.md](CONTRIBUTING.md)
- 安全说明：[SECURITY.md](SECURITY.md)
- 许可证：[LICENSE](LICENSE)
- 归属说明：[NOTICE](NOTICE)

如果提交性能问题，请同时提供 `diagnose.ps1` 结果、视频编码/分辨率/帧率、字幕区域、最终 SRT 条数和总耗时。
