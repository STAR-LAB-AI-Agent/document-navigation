# -*- coding: utf-8 -*-
"""用 PyMuPDF 生成一个带多级标题与列表的样例 PDF,作为第二个测试文档。

直接运行本脚本即可生成/覆盖 test_data/sample_report.pdf。
测试会自动调用 ensure_sample_pdf(),无需手动执行。
"""
from pathlib import Path

import pymupdf

SAMPLE_PATH = Path(__file__).resolve().parent.parent / "test_data" / "sample_report.pdf"

CONTENT = [
    # (页码, [ (字号, 文本, 是否加粗) ... ])
    (1, [
        (22, "样例技术报告", True),
        (12, "用于测试文档结构解析的多页样例文档。", False),
        (18, "第一章 项目概述", True),
        (12, "本章介绍样例项目的整体背景与目标范围,以及术语约定。", False),
        (12, "项目起源于对大量本地文档进行自动化整理的实际需求。", False),
    ]),
    (2, [
        (15, "1.1 背景", True),
        (12, "随着文档数量增长,需要自动化的结构导航能力来提高阅读效率。", False),
        (12, "传统的人工翻页检索耗时且容易遗漏关键内容。", False),
        (15, "1.2 目标", True),
        (12, "本项目的主要目标包括以下三点:", False),
        (12, "解析文档标题与章节结构", False),
        (12, "生成带页码的目录", False),
        (12, "支持按指令定位章节或页面", False),
    ]),
    (3, [
        (18, "第二章 实现方案", True),
        (12, "本章描述系统的模块划分与数据处理流程。", False),
        (12, "结构解析模块负责识别标题与列表。", False),
        (12, "目录生成模块负责输出层级目录。", False),
    ]),
    (4, [
        (18, "附录 术语表", True),
        (12, "附录收录正文出现的关键术语及其解释,便于查阅。", False),
    ]),
]


def make_sample_pdf(path: Path = SAMPLE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    for page_no, lines in CONTENT:
        page = doc.new_page()
        y = 72.0
        for size, text, bold in lines:
            fontname = "china-s" if not bold else "china-ss"
            try:
                page.insert_text((72, y), text, fontsize=size, fontname=fontname)
            except Exception:
                font = pymupdf.Font(fontfile="C:/Windows/Fonts/msyh.ttc")
                page.insert_font(fontname="msyh", fontbuffer=font.buffer)
                page.insert_text((72, y), text, fontsize=size, fontname="msyh")
            y += size + 10
    doc.save(str(path))
    doc.close()
    return path


def ensure_sample_pdf() -> Path:
    """测试用:样例不存在时自动生成。"""
    if not SAMPLE_PATH.exists():
        return make_sample_pdf()
    return SAMPLE_PATH


if __name__ == "__main__":
    print(f"generated: {make_sample_pdf()}")
