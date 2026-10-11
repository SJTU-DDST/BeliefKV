# BeliefKV 可编辑架构图

报告周期：2026-08-15 至 2026-10-11。

## PowerPoint

- `beliefkv_architecture_editable_zh.pptx`：三页中文配套图。
- `beliefkv_architecture_editable_en.pptx`：同样三页的英文版本。

三页依次为系统架构、预测与调度协同、增量迁移与 Child–Parent 容量交接。
文字、模块、页面、线条和箭头均为独立 DrawingML 对象，没有平铺图片。
折线为可编辑自由形状，直线为 PowerPoint 连接线；图形和文字可分别选择、
移动、改色或修改内容。每页备注包含图义和适用边界。

页面尺寸为 14.4 × 10.1 英寸，贴合配图比例。复制到报告幻灯片时可多选
或组合整张图后等比缩放，也可直接编辑布局。中文使用 Noto Sans CJK SC，
英文使用 DejaVu Sans；源字体在当前机器已安装，PPT 未嵌入字体。

`pptx_preview_zh.png` 和 `pptx_preview_en.png` 是从实际 PowerPoint
经 LibreOffice 渲染得到的三页预览，便于快速检查版面。
`powerpoint_validation.json` 记录文本与 SVG 一致性、页面边界、
原生对象数量、文件 SHA256 和办公软件渲染检查结果。

## 论文输出

每张图均提供 SVG、矢量 PDF 和 600 dpi PNG：

| 文件前缀 | 内容 |
| --- | --- |
| `01_system_overview` | 系统架构 |
| `02_prediction_and_overlap` | 预测与调度协同 |
| `03_pages_and_capacity_handoff` | 增量迁移与容量交接 |

`_zh` 与 `_en` 分别表示中文、英文。PDF 宽 7.2 英寸，PNG 宽 4320 像素，
适于论文双栏全宽图。`beliefkv_figures_bilingual.pdf` 合并全部六张矢量图；
`preview.png` 是英文快速预览。`manifest.json` 保存架构基线修订、
生成器和绘图 helper 的 SHA256、报告周期、画布尺寸、输出 DPI
与 PowerPoint 原生对象数量。

时序为机制示意，不是实测性能图；传输 ACK 与首次服务分别展示，
不将需求 handoff 计为完成边界前的预测 H2D。图 3 仅使用已结束且可安全
释放的 child 私有容量；最后一个 child 自身的空间只能在其结束后使用。

## 重新生成

生成器：`docs/figures/generate_monthly_paper_figures.py`。
依赖 Python 3.11+、Pillow、CairoSVG、python-pptx，以及系统 `pdfunite`
和上述字体。当前依赖隔离在临时目录，没有修改实验环境：

```bash
PYTHONPATH=/tmp/beliefkv-monthly-render-packages-20261011 \
  /home/longhao/miniconda3/envs/beliefkv-next/bin/python \
  docs/figures/generate_monthly_paper_figures.py
```

若临时目录已清理，可在独立环境安装依赖后运行同一生成器。
办公软件预览和校验回执是本次交付的检查产物；修改图源后应重新渲染检查。
