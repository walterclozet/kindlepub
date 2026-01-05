# KindlePub

[English Version](./README_en.md) | 中文版

高性能图片/CBZ 转 EPUB 转换器，专为 **Send to Kindle** 优化。

## 特色功能
- **构图保护**：默认不裁边，完美保留画册（如 Love Live!）的原版非居中排版和艺术留白。
- **性能卓越**：多线程并行处理，自动预留 1 个核心确保系统流畅。
- **智能缩放**：自动限制图片高度为 2048px (LANCZOS)，平衡清晰度与加载速度。
- **元数据智能提取**：支持 `ComicInfo.xml`，若缺失则提供交互式询问。
- **Fixed Layout**：生成的 EPUB 3 为固定版式，完美适配 Kindle 官方推送服务。

## 安装
pip install -r requirements.txt

## 使用
python3 epub.py [文件夹或ZIP/CBZ] [输出名]
