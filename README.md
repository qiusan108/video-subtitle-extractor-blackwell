简体中文 | [English](README_en.md)

# 字幕工坊

一款 Windows 桌面工具：提取视频画面中的字幕、同步字幕时间轴，也能将 SRT/ASS 字幕永久烧录进视频。基于 [YaoFANGUK/video-subtitle-extractor](https://github.com/YaoFANGUK/video-subtitle-extractor) 开发，针对 NVIDIA RTX 50 / Blackwell 显卡优化提取流程。

![字幕工坊的字幕烧录界面](design/subtitle_studio_preview.png)

## 能做什么

- **提取字幕**：识别视频画面中的硬字幕，生成 SRT/TXT；支持自动、快速、精准及实验性的智能模式。
- **同步时间轴**：调整已有字幕与视频的时间对应关系。
- **烧录字幕**：将 SRT/ASS 永久嵌入视频画面。SRT 可设置字体、字号、颜色、描边、阴影和底部边距；ASS 保留原有样式。支持 CPU 或 NVIDIA 编码、进度显示与取消。
- **本机处理**：视频、字幕和 OCR 均在本机处理；无需上传视频。

## 获取与运行

从 [v0.3.0 下载页](https://github.com/qiusan108/video-subtitle-extractor-blackwell/releases/tag/v0.3.0-blackwell)获取新版源码压缩包，包含上图的新界面和字幕烧录功能。旧的 v0.2.0 不包含烧录功能。目前仍没有免安装 EXE。

在 Windows 10/11 x64 上运行，需要 Python 3.12 x64 和 FFmpeg。提取功能需要相应 NVIDIA 驱动；烧录功能要求 FFmpeg 包含 libass。解压新版源码包后，在项目目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\setup-blackwell.ps1
.\open.bat
```

首次配置会下载 OCR 模型和原生运行组件，并校验下载文件；Python 依赖另由 pip 安装。FFmpeg 可放在系统 PATH、项目内或相邻的 `ffmpeg` 目录，也可用 `VSE_FFMPEG_PATH` 指定。如果安装失败，可运行 `diagnose.ps1` 查看环境信息。

## 使用提示

- 提取时，先框选尽量准确的字幕区域。智能模式仍属实验功能；已公开的 RTX 5060 性能数据只对应原自动模式。
- 烧录时选择视频、SRT/ASS 和输出路径。默认尽量接近原视频大小，但结果不会精确一致；“画质优先”模式可能产生更大的文件。
- 烧录需要重新编码视频。长视频完成时间取决于显卡、编码器和画质；ASS 的内嵌样式不会被界面的 SRT 样式选项覆盖。

## 开源与反馈

本项目延续上游的 [Apache-2.0 许可证](LICENSE)，并保留[上游归属说明](NOTICE)。更多开发与测试记录见 [CHANGELOG](CHANGELOG.md)；遇到问题可提交 [Issue](https://github.com/qiusan108/video-subtitle-extractor-blackwell/issues)，请勿上传无权公开的视频或字幕。
