# Security Policy

请通过 GitHub Private Vulnerability Reporting 私下报告安全问题。公开 Issue 中不要
提交私人视频、完整文件路径、用户名、访问令牌或其他敏感数据。

本分支会启动 FFmpeg/FFprobe 并读取用户选择的视频。程序不会为视频路径拼接
Shell 命令，而是以参数列表启动子进程。请仅使用可信来源的 FFmpeg 构建，并保持
NVIDIA 驱动、PaddlePaddle、PaddleOCR 与 Python 依赖更新。

模型文件和发布包应提供 SHA-256；不要运行来源不明的安装脚本或模型文件。
