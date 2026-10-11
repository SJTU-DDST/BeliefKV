#!/usr/bin/env python3
"""Build the monthly report using the supplied PowerPoint template."""

from copy import deepcopy
from pathlib import Path

from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.oxml.xmlchemy import OxmlElement
from pptx.util import Inches, Pt


REPORT_DIR = Path(__file__).resolve().parents[1] / "reports" / "2026-10"
TEMPLATE = REPORT_DIR / "月度汇报模板.pptx"
OUTPUT = REPORT_DIR / "BeliefKV_月度汇报_20261011.pptx"
ARCHITECTURE = REPORT_DIR / "assets" / "beliefkv_architecture_editable_zh.pptx"
FONT = "微软雅黑"
NAVY = "162A46"
BLUE = "01639C"
TEAL = "357B73"
GRAY = "596875"
RULE = "D6DFE7"
WHITE = "FFFFFF"


def format_paragraph(paragraph, size, color, bold=False, align=PP_ALIGN.LEFT):
    paragraph.alignment = align
    paragraph.line_spacing = 1.12
    paragraph.space_before = Pt(0)
    paragraph.space_after = Pt(0)
    props = paragraph._p.get_or_add_pPr()
    props.set("marL", "0")
    props.set("indent", "0")
    props.append(OxmlElement("a:buNone"))
    for run in paragraph.runs:
        run.font.name = FONT
        run.font.size = Pt(size)
        run.font.bold = bold
        run.font.color.rgb = RGBColor.from_string(color)
        run_props = run._r.get_or_add_rPr()
        run_props.set("lang", "zh-CN")
        run_props.set("spc", "0")
        east_asian = run_props.find(
            "{http://schemas.openxmlformats.org/drawingml/2006/main}ea"
        )
        if east_asian is None:
            east_asian = OxmlElement("a:ea")
            run_props.append(east_asian)
        east_asian.set("typeface", FONT)


def text(slide, value, x, y, width, height, size=20, color=NAVY,
         bold=False, align=PP_ALIGN.LEFT):
    shape = slide.shapes.add_textbox(
        Inches(x), Inches(y), Inches(width), Inches(height)
    )
    frame = shape.text_frame
    frame.clear()
    frame.margin_left = frame.margin_right = 0
    frame.margin_top = frame.margin_bottom = 0
    frame.word_wrap = True
    frame.auto_size = MSO_AUTO_SIZE.NONE
    frame.vertical_anchor = MSO_ANCHOR.TOP
    frame.text = value
    for paragraph in frame.paragraphs:
        format_paragraph(paragraph, size, color, bold, align)
    return shape


def rectangle(slide, x, y, width, height, fill, stroke=RULE):
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, Inches(x), Inches(y),
        Inches(width), Inches(height)
    )
    shape.fill.solid()
    shape.fill.fore_color.rgb = RGBColor.from_string(fill)
    shape.line.color.rgb = RGBColor.from_string(stroke)
    shape.line.width = Pt(1)
    remove_shadow(shape)
    return shape


def remove_shadow(shape):
    shape._element.spPr.append(OxmlElement("a:effectLst"))
    effect_ref = shape._element.find(
        "{http://schemas.openxmlformats.org/presentationml/2006/main}style/"
        "{http://schemas.openxmlformats.org/drawingml/2006/main}effectRef"
    )
    if effect_ref is not None:
        effect_ref.set("idx", "0")


def line(slide, x1, y1, x2, y2, color=RULE, arrow=False, width=1.5):
    shape = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT,
        Inches(x1), Inches(y1), Inches(x2), Inches(y2)
    )
    shape.line.color.rgb = RGBColor.from_string(color)
    shape.line.width = Pt(width)
    remove_shadow(shape)
    if arrow:
        end = OxmlElement("a:tailEnd")
        end.set("type", "triangle")
        end.set("w", "sm")
        end.set("len", "sm")
        shape._element.spPr.get_or_add_ln().append(end)
    return shape


def add_slide(presentation, layout, title, notes):
    slide = presentation.slides.add_slide(layout)
    for shape in list(slide.shapes):
        if shape != slide.shapes.title:
            shape._element.getparent().remove(shape._element)
    heading = slide.shapes.title
    heading.text = title
    for paragraph in heading.text_frame.paragraphs:
        format_paragraph(paragraph, 42, WHITE, bold=True)
    text(
        slide, "BeliefKV  |  2026.08.15 - 2026.10.11",
        7.90, 6.80, 4.10, 0.25, size=12, color=GRAY,
        align=PP_ALIGN.RIGHT
    )
    slide.notes_slide.notes_text_frame.text = notes
    return slide


def project_slide(presentation, layout):
    slide = add_slide(
        presentation, layout, "项目介绍",
        "建议用时：40 秒。\n\n"
        "BeliefKV 面向动态多 Agent 工作流的 KV 管理与调度。"
        "父任务创建子任务、调用工具，等待期间 KV 可以迁到 Host，恢复前再搬回 GPU。\n"
        "我们根据收尾通知、正文与执行进度提供完成提示；"
        "运行时结合容量、缓存副本与准入机会，提前备份、预取并安排执行，"
        "减少等待与重复搬运，提高吞吐、缩短完成时间。"
    )
    text(slide, "BeliefKV", 0.75, 1.78, 4.25, 0.55,
         size=31, color=BLUE, bold=True)
    text(slide, "动态 Agent 工作流的\n预测式 KV 管理与调度",
         0.75, 2.40, 4.3, 0.85, size=22, bold=True)

    for y, label, body in (
        (3.42, "场景", "子任务、工具与 JOIN 交错，\nKV 在 HBM / Host 间迁移。"),
        (4.45, "方法", "依据收尾信号与执行进度，\n提前备份、预取并安排执行。"),
        (5.48, "目标", "减少恢复等待与重复搬运，\n提高吞吐、降低完成时间。"),
    ):
        text(slide, label, 0.75, y, 0.70, 0.4,
             size=21, color=BLUE, bold=True)
        text(slide, body, 1.58, y, 3.64, 0.80, size=19)

    # Distill the existing architecture figure into readable editable modules.
    rectangle(slide, 5.58, 1.90, 7.08, 1.10, "EFF5FA", "ADC5D7")
    text(slide, "动态工作流：子任务与工具调用",
         5.78, 2.06, 6.67, 0.40, size=21, color=BLUE, bold=True,
         align=PP_ALIGN.CENTER)
    for x, width, label in (
        (5.83, 1.25, "父 Agent"),
        (7.61, 1.25, "子 Agent"),
        (9.39, 1.12, "JOIN"),
        (11.05, 1.34, "父 Agent"),
    ):
        text(slide, label, x, 2.53, width, 0.32,
             size=17, align=PP_ALIGN.CENTER)
    for x1, x2 in ((7.13, 7.52), (8.91, 9.30), (10.58, 10.97)):
        line(slide, x1, 2.69, x2, 2.69, BLUE, arrow=True)

    line(slide, 7.25, 3.02, 7.25, 3.42, BLUE, arrow=True)
    rectangle(slide, 5.58, 3.43, 3.34, 1.10, "FBF6EB", "D5BC89")
    text(slide, "完成预测", 5.78, 3.60, 2.94, 0.40,
         size=22, color="926923", bold=True, align=PP_ALIGN.CENTER)
    text(slide, "通知 / 正文 / 执行进度",
         5.74, 4.10, 3.00, 0.28, size=16, color=GRAY,
         align=PP_ALIGN.CENTER)

    line(slide, 8.97, 3.96, 9.29, 3.96, TEAL, arrow=True)
    rectangle(slide, 9.35, 3.43, 3.31, 1.10, "EEF6F3", "ABC9BE")
    text(slide, "迁移与准入决策", 9.50, 3.60, 3.00, 0.40,
         size=22, color=TEAL, bold=True, align=PP_ALIGN.CENTER)
    text(slide, "容量 / 缺失页 / 执行机会",
         9.50, 4.10, 3.00, 0.28, size=15.5, color=GRAY,
         align=PP_ALIGN.CENTER)
    line(slide, 11.0, 4.55, 11.0, 4.91, TEAL, arrow=True)

    rectangle(slide, 5.58, 4.93, 7.08, 1.37, WHITE, "ADC5D7")
    text(slide, "SGLang 原生缓存与 GPU 执行",
         5.78, 5.07, 6.67, 0.42, size=22, color=BLUE, bold=True,
         align=PP_ALIGN.CENTER)
    rectangle(slide, 5.89, 5.69, 2.30, 0.45, "EFF5FA", "ADC5D7")
    rectangle(slide, 10.03, 5.69, 2.30, 0.45, "EEF6F3", "ABC9BE")
    text(slide, "GPU / HBM", 5.99, 5.75, 2.10, 0.34,
         size=19, color=BLUE, bold=True, align=PP_ALIGN.CENTER)
    text(slide, "CPU / Host", 10.13, 5.75, 2.10, 0.34,
         size=19, color=TEAL, bold=True, align=PP_ALIGN.CENTER)
    text(slide, "D2H", 8.51, 5.49, 1.20, 0.22,
         size=12, color=TEAL, align=PP_ALIGN.CENTER)
    text(slide, "H2D", 8.51, 6.11, 1.20, 0.22,
         size=12, color=BLUE, align=PP_ALIGN.CENTER)
    line(slide, 8.31, 5.80, 9.91, 5.80, TEAL, arrow=True)
    line(slide, 9.91, 6.00, 8.31, 6.00, BLUE, arrow=True)
    text(slide, "按缺失页迁移，恢复后及时执行",
         5.78, 6.43, 6.67, 0.32, size=16, color=GRAY,
         align=PP_ALIGN.CENTER)


def progress_slide(presentation, layout):
    slide = add_slide(
        presentation, layout, "上月工作总结",
        "建议用时：50 秒。汇报周期为 2026 年 8 月 15 日至 10 月 11 日。\n\n"
        "八月下半月建立因果图、原生子任务与父任务上下文连续性。"
        "九月上中旬贯通预测迁移、传输确认与生命周期。\n"
        "九月下旬适配 FULL / Mamba，完成训练与项目隔离校准；"
        "随后转向通知、语义与 decode 进度，分离剩余工作和排队等待。\n"
        "十月推进原生对照与两波到达，优化增量备份、按页恢复、准入与容量交接。"
    )
    milestones = (
        ("08.15 - 08.31", "建立工作流与调度基础",
         "实现因果前沿、原生 subagent 与 JOIN 后 parent 上下文连续性。"),
        ("09.01 - 09.21", "贯通预测式 KV 迁移",
         "接通预测 D2H / H2D、传输 ACK、驻留租约与物理恢复生命周期。"),
        ("09.22 - 09.27", "完成混合缓存适配与离线建模",
         "适配 FULL / Mamba；完善传输埋点，完成训练与项目隔离校准。"),
        ("09.28 - 10.04", "转向事件与内容驱动的预测",
         "结合通知、语义、长度与 decode 进度，建立阶段与剩余工作预测。"),
        ("10.05 - 10.11", "推进原生对照与系统性能优化",
         "原生对照与两波到达；优化增量备份、缺失页恢复、准入与容量交接。"),
    )
    line(slide, 2.91, 2.03, 2.91, 5.40, RULE, width=2)
    for index, (date, title, detail) in enumerate(milestones):
        y = 1.80 + index * 0.84
        color = TEAL if index == 4 else BLUE
        text(slide, date, 0.78, y + 0.16, 1.96, 0.42,
             size=19, color=color, bold=True)
        marker = slide.shapes.add_shape(
            MSO_SHAPE.OVAL, Inches(2.85), Inches(y + 0.23),
            Inches(0.12), Inches(0.12)
        )
        marker.fill.solid()
        marker.fill.fore_color.rgb = RGBColor.from_string(color)
        marker.line.fill.background()
        remove_shadow(marker)
        text(slide, title, 3.24, y, 9.25, 0.43,
             size=22, color=color, bold=True)
        text(slide, detail, 3.24, y + 0.43, 9.25, 0.38,
             size=18, color=GRAY)
    line(slide, 0.78, 6.08, 12.49, 6.08, RULE)
    text(slide, "目前：机制链路已贯通，稳定的端到端性能优势仍待验证。",
         0.78, 6.25, 11.71, 0.34, size=19, color=NAVY, bold=True)


def scale_figure_element(element, scale):
    for transform in element.iter(qn("a:xfrm")):
        for coordinate in transform:
            for key in ("x", "y", "cx", "cy"):
                if key in coordinate.attrib:
                    coordinate.set(key, str(round(int(coordinate.get(key)) * scale)))
    length_attributes = {
        qn("a:ln"): ("w",),
        qn("a:bodyPr"): ("lIns", "rIns", "tIns", "bIns", "spcCol"),
        qn("a:pPr"): ("marL", "marR", "indent", "defTabSz"),
        qn("a:rPr"): ("sz", "spc"),
        qn("a:defRPr"): ("sz", "spc"),
        qn("a:endParaRPr"): ("sz", "spc"),
        qn("a:spcPts"): ("val",),
    }
    for properties in element.iter():
        for key in length_attributes.get(properties.tag, ()):
            if key in properties.attrib:
                properties.set(key, str(round(int(properties.get(key)) * scale)))


def import_figure(slide, source_slide, source_size, x, y, width):
    group = slide.shapes.add_group_shape()
    group.name = "可编辑系统架构图"
    source_width, source_height = source_size
    scale = Inches(width) / source_width
    next_id = slide.shapes._next_shape_id
    for source_shape in source_slide.shapes:
        element = deepcopy(source_shape._element)
        for properties in element.iter(qn("p:cNvPr")):
            properties.set("id", str(next_id))
            next_id += 1
        # Scale text metrics explicitly so Office renderers agree on group sizing.
        scale_figure_element(element, scale)
        group.shapes._spTree.append(element)
    height = width * source_height / source_width
    transform = group._element.grpSpPr.get_or_add_xfrm()
    for tag, values in (
        ("off", {"x": Inches(x), "y": Inches(y)}),
        ("ext", {"cx": Inches(width), "cy": Inches(height)}),
        ("chOff", {"x": 0, "y": 0}),
        ("chExt", {"cx": Inches(width), "cy": Inches(height)}),
    ):
        element = transform.find(qn(f"a:{tag}"))
        for key, value in values.items():
            element.set(key, str(int(value)))
    return group


def architecture_overview_slide(presentation, layout, figures):
    slide = add_slide(
        presentation, layout, "关键进展：系统总体架构",
        "建议用时：25 秒。\n\n"
        "这张图展示已经贯通的系统架构。"
        "工作流事件维护动态依赖，通知、正文和服务进度提供完成预测。"
        "运行时结合有效副本、容量与准入机会决定迁移；"
        "SGLang 负责物理缓存与实际 GPU 执行。"
    )
    size = (figures.slide_width, figures.slide_height)
    import_figure(slide, figures.slides[0], size, 0.60, 1.64, 7.00)
    text(slide, "从动态工作流到\n物理缓存与执行",
         7.97, 1.96, 4.50, 0.91, size=25, color=BLUE, bold=True)
    milestones = (
        ("捕获动态执行依赖",
         "关联子任务、工具与 JOIN 事件，\n维护上下文和当前可执行状态。"),
        ("融合完成信号与运行进度",
         "结合通知、正文与 decode 进度，\n滚动预测 child / tool 的完成过程。"),
        ("连接迁移决策与原生执行",
         "按缺失页迁移，协调驻留与准入；\n由 SGLang 管理物理数据依赖。"),
    )
    for index, (title, detail) in enumerate(milestones):
        y = 3.13 + index * 1.10
        text(slide, title, 7.97, y, 4.60, 0.42,
             size=22, color=TEAL, bold=True)
        text(slide, detail, 7.97, y + 0.48, 4.60, 0.67,
             size=17.5, color=GRAY)


def architecture_coordination_slide(presentation, layout, figures):
    slide = add_slide(
        presentation, layout, "关键进展：预测与迁移协同",
        "建议用时：30 秒。\n\n"
        "左图展示预测与调度协同：用实时完成信号滚动更新，"
        "将剩余工作、执行机会和物理恢复依赖分开处理。\n"
        "右图展示增量迁移与容量交接：FULL 只备份缺失部分，"
        "Mamba 恢复必要状态；已返回 child 的私有页及时回收，"
        "让 parent 恢复与准入衔接。"
    )
    size = (figures.slide_width, figures.slide_height)
    captions = (
        ("预测与调度协同", "完成信号 → 滚动预测 → 恢复与执行", BLUE),
        ("增量迁移与容量交接", "缺失页备份 → child 回收 → parent 恢复", TEAL),
    )
    for index, (title, caption, color) in enumerate(captions):
        x = 0.65 + index * 6.25
        text(slide, title, x, 1.78, 5.80, 0.42,
             size=22, color=color, bold=True, align=PP_ALIGN.CENTER)
        import_figure(slide, figures.slides[index + 1], size, x, 2.24, 5.80)
        text(slide, caption, x, 6.34, 5.80, 0.28,
             size=16, color=GRAY, align=PP_ALIGN.CENTER)
    line(slide, 6.68, 1.86, 6.68, 6.45, RULE, width=1)


def goals_slide(presentation, layout):
    slide = add_slide(
        presentation, layout, "下月目标",
        "建议用时：35 秒。\n\n"
        "下一步提高实际可复用的预取覆盖；联动回收、驻留与准入，"
        "降低重复加载和控制成本；以同版本原生 SGLang 对照和多轮平均，"
        "报告吞吐、配对 JCT 与重算。\n"
        "目标是得到稳定、可归因的端到端收益，形成可写入论文的性能证据。"
    )
    text(slide, "聚焦：让预测式 KV 迁移带来可验证的端到端收益",
         0.78, 1.87, 11.73, 0.55, size=24, color=BLUE, bold=True)
    goals = (
        ("提高有效预取覆盖",
         "融合通知、正文与 decode 进度，改善 child / tool 完成预测。\n"
         "覆盖更多 Host-only FULL 页，统计返回前恢复与下一请求实际复用。"),
        ("恢复与调度协同，降低额外开销",
         "联动容量回收、驻留与准入，减少重复加载和 ACK 后排队。\n"
         "削减候选检查、前缀扫描与控制交付成本，避免抵消迁移收益。"),
        ("建立公平、稳定的性能证据",
         "同版本、同负载对比原生 SGLang，正式实验采用多轮平均。\n"
         "综合报告繁忙窗口吞吐、成功工作流吞吐、配对 JCT 与重算。"),
    )
    for index, (title, detail) in enumerate(goals, 1):
        y = 2.66 + (index - 1) * 1.16
        text(slide, f"{index:02}", 0.82, y - 0.02, 0.73, 0.60,
             size=28, color=BLUE, bold=True)
        text(slide, title, 1.86, y, 10.60, 0.48,
             size=23, color=NAVY, bold=True)
        text(slide, detail, 1.86, y + 0.47, 10.60, 0.65,
             size=18, color=GRAY)
    line(slide, 0.78, 6.16, 12.49, 6.16, RULE)
    text(slide, "验收：提高繁忙窗口与成功工作流吞吐，同时降低完成时间。",
         0.78, 6.31, 11.71, 0.32, size=18, color=TEAL, bold=True)


def main():
    presentation = Presentation(TEMPLATE)
    layout = presentation.slides[1].slide_layout
    # Keep the template's master, theme, header and logo; replace all old slides.
    for slide_id in list(presentation.slides._sldIdLst):
        presentation.part.drop_rel(slide_id.rId)
        presentation.slides._sldIdLst.remove(slide_id)
    project_slide(presentation, layout)
    progress_slide(presentation, layout)
    figures = Presentation(ARCHITECTURE)
    architecture_overview_slide(presentation, layout, figures)
    architecture_coordination_slide(presentation, layout, figures)
    goals_slide(presentation, layout)
    presentation.core_properties.title = "BeliefKV 月度汇报"
    presentation.core_properties.subject = "2026 年 8 月 15 日至 10 月 11 日"
    presentation.core_properties.keywords = "BeliefKV, 动态 Agent, KV 缓存, 预测式调度"
    presentation.core_properties.comments = "五页正文；演讲者备注合计约三分钟。"
    presentation.save(OUTPUT)
    print(OUTPUT)


if __name__ == "__main__":
    main()
