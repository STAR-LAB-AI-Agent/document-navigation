#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doc-navigator:AI 文档结构导航(实验第 10 题)
================================================

基于 Docling 解析文本型 PDF 的标题、章节和列表结构,生成目录,
并根据自然语言指令定位目标章节或页面,支持跳转打开对应页面。

子命令:
    parse   <pdf>                  解析文档结构(标题/章节树/列表)
    toc     <pdf> [--json]         生成目录(默认 Markdown,--json 结构化)
    locate  <pdf> --query "..."/--page N   定位章节或页面
    open    <pdf> --page N/--section "..." 生成跳转链接(--launch 实际打开)

统一输出 JSON(status/message/data),退出码:0 成功 2 未找到 3 文件错误 4 解析/执行失败。
页码一律为 PDF 物理页码(从 1 开始)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import webbrowser
from collections import OrderedDict
from datetime import datetime
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = SKILL_ROOT / "logs"
CACHE_DIR = SKILL_ROOT / ".cache"

EXIT_OK = 0
EXIT_NO_MATCH = 2
EXIT_FILE = 3
EXIT_PARSE = 4

# docling 导入较重,统一放到函数内延迟导入
_EXCLUDE_PAGE_DECOR = {"PAGE_HEADER", "PAGE_FOOTER"}


# --------------------------------------------------------------------------
# 日志
# --------------------------------------------------------------------------
def log_line(command: str, pdf: str, elapsed: float, status: str, detail: str) -> None:
    """追加一行运行日志。只记录命令、文档名、耗时与结果摘要,不记录任何密钥类信息。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(LOG_DIR / "doc_navigator.log", "a", encoding="utf-8") as f:
            f.write(f"{stamp} | {command:<7} | {Path(pdf).name} | "
                    f"{elapsed:6.2f}s | {status:<8} | {detail}\n")
    except OSError:
        pass  # 日志失败不影响主流程


# --------------------------------------------------------------------------
# 文本规范化
# --------------------------------------------------------------------------
_WS = re.compile(r"\s+")


def norm_text(s: str) -> str:
    """去除全部空白字符(含全角空格),用于中文标题/正文匹配。"""
    return _WS.sub("", s or "")


def tokens_of(query: str) -> list[str]:
    """把自然语言查询切成词块(中英文连续段),用于全文检索的兜底匹配。"""
    return re.findall(r"[0-9A-Za-z\u4e00-\u9fff]+", query or "")


# --------------------------------------------------------------------------
# 标题层级推断:docling 对多数 PDF 不输出标题级别,按编号规则推断
# ("第X章"、"一、"、"1." 等常见一级标题形式均落入默认层级,无需单独匹配)
# --------------------------------------------------------------------------
_P_MULTI = re.compile(r"^([0-9]+(?:\.[0-9]+)+)(?![0-9])")            # 4.1 / 1.2.3


def infer_heading_level(title: str) -> int:
    """按常见编号约定推断标题层级;无法识别时默认 1 级。

    约定:4.1 → 2 级;1.2.3 → 3 级;"第X章"、"一、"、"1." 及其余形式 → 1 级。
    """
    m = _P_MULTI.match(title.strip())
    return len(m.group(1).split(".")) if m else 1


# --------------------------------------------------------------------------
# PDF 解析(带 SHA-256 结果缓存)
# --------------------------------------------------------------------------
def _load_docling_document(pdf_path: Path):
    try:
        from docling.document_converter import DocumentConverter, PdfFormatOption
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import PdfPipelineOptions
    except ImportError as exc:
        raise RuntimeError(
            "未找到 docling 库:skill 自带环境尚未初始化。"
            "请用系统任意 Python ≥3.10 运行一次 scripts/setup_env.py 完成首次安装,"
            "之后改用 <skill_dir>/.venv 的解释器重试。"
            f"(原始错误:{exc})"
        ) from exc

    opts = PdfPipelineOptions()
    opts.do_ocr = False            # 仅处理文本型 PDF,不启用 OCR
    opts.do_table_structure = False  # 不下载/运行表格结构模型,减小依赖与耗时
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    return converter.convert(str(pdf_path)).document


def _sha256_of(pdf_path: Path) -> str:
    h = hashlib.sha256()
    with open(pdf_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_with_docling(pdf_path: Path) -> dict:
    try:
        import docling
        from docling_core.types.doc.labels import DocItemLabel
    except ImportError as exc:
        raise RuntimeError(
            "未找到 docling-core 库:skill 自带环境尚未初始化。"
            "请用系统任意 Python ≥3.10 运行一次 scripts/setup_env.py 完成首次安装。"
            f"(原始错误:{exc})"
        ) from exc

    doc = _load_docling_document(pdf_path)
    num_pages = doc.num_pages()

    headings: list[dict] = []       # 顺序章节标题
    section_texts: list[str] = []   # 每个标题到下一个标题之间的正文(与 headings 对齐)
    list_items: list[dict] = []     # 列表项 {page, text}
    page_texts: dict[int, list[str]] = OrderedDict()

    cur_text: list[str] = []
    for item, _level in doc.iterate_items():
        label = getattr(item, "label", None)
        label = label.name if label is not None else ""
        text = (getattr(item, "text", "") or "").replace("\xa0", " ").strip()
        prov = getattr(item, "prov", None) or []
        page = prov[0].page_no if prov else None

        if label in _EXCLUDE_PAGE_DECOR or not text:
            continue

        if label in ("TITLE", "SECTION_HEADER"):
            if headings:
                section_texts.append(" ".join(cur_text))
            headings.append({"seq": len(headings), "title": text, "page": page})
            cur_text = [text]
        else:
            cur_text.append(text)
            if label == "LIST_ITEM":
                list_items.append({"page": page, "text": text})
        if page is not None:
            page_texts.setdefault(page, []).append(text)
    if headings:
        section_texts.append(" ".join(cur_text))

    # 文档标题:第一个标题(通常在首页);它同时保留在章节列表中并带 is_title 标记
    doc_title = headings[0]["title"] if headings else pdf_path.stem

    structure = {
        "docling_version": docling.__version__,
        "num_pages": num_pages,
        "title": doc_title,
        "headings": headings,
        "section_texts": section_texts,
        "list_items": list_items,
        "page_texts": {str(p): " ".join(t) for p, t in page_texts.items()},
    }
    return structure


def parse_pdf(pdf_path: Path, use_cache: bool = True) -> tuple[dict, str]:
    """解析 PDF 结构。返回 (structure, cache_status),cache_status ∈ hit/miss/disabled。

    解析结果按文件 SHA-256 + docling 版本缓存,同一文档重复定位时无需重新解析。
    """
    try:
        import docling
    except ImportError as exc:
        raise RuntimeError(
            "未找到 docling 库:skill 自带环境尚未初始化。"
            "请用系统任意 Python ≥3.10 运行一次 scripts/setup_env.py 完成首次安装,"
            "之后改用 <skill_dir>/.venv 的解释器重试。"
            f"(原始错误:{exc})"
        ) from exc

    sha = _sha256_of(pdf_path)
    cache_file = CACHE_DIR / f"{sha[:16]}-v{docling.__version__}.json"
    if use_cache and cache_file.exists():
        try:
            with open(cache_file, encoding="utf-8") as f:
                cached = json.load(f)
            if cached.get("sha256") == sha and cached.get("docling_version") == docling.__version__:
                return cached["structure"], "hit"
        except (OSError, ValueError, KeyError):
            pass  # 缓存损坏则重新解析

    structure = _parse_with_docling(pdf_path)
    if use_cache:
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump({"sha256": sha, "docling_version": docling.__version__,
                           "structure": structure}, f, ensure_ascii=False)
        except OSError:
            pass  # 缓存写失败不影响结果
    return structure, "miss"


# --------------------------------------------------------------------------
# 章节树构建
# --------------------------------------------------------------------------
def build_sections(structure: dict) -> list[dict]:
    """把扁平标题列表补全为章节信息:层级、路径、页码范围、列表统计。"""
    headings = structure["headings"]
    section_texts = structure["section_texts"]
    num_pages = structure["num_pages"]
    if not headings:
        return []

    levels = [infer_heading_level(h["title"]) for h in headings]
    min_level = min(levels)
    levels = [lv - min_level + 1 for lv in levels]  # 归一化:最小层级 = 1

    sections: list[dict] = []
    stack: list[str] = []  # 当前各级标题(1 起的层级)
    for i, h in enumerate(headings):
        level = levels[i]
        # 回退到本级:弹出更深层级
        del stack[level - 1:]
        stack.append(h["title"])
        page = h["page"] or 1
        # 结束页 = 下一个同级或更高级标题的起始页 - 1(兜底为文档最后一页)
        page_end = num_pages
        for j in range(i + 1, len(headings)):
            if levels[j] <= level:
                page_end = max(page, (headings[j]["page"] or page) - 1)
                break
        sections.append({
            "seq": h["seq"],
            "title": h["title"],
            "level": level,
            "path": " > ".join(stack),
            "page_start": page,
            "page_end": page_end,
            "is_title": i == 0 and len(headings) > 1,
            "section_text": section_texts[i] if i < len(section_texts) else "",
        })

    for s in sections:
        s["list_count"] = sum(
            1 for it in structure["list_items"]
            if it["page"] is not None and s["page_start"] <= it["page"] <= s["page_end"]
        )
    return sections


# --------------------------------------------------------------------------
# 输出辅助
# --------------------------------------------------------------------------
def out_json(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ": "))


def make_result(status: str, message: str, data: dict | None = None,
                **common) -> dict:
    r = {"status": status, "message": message}
    if data is not None:
        r["data"] = data
    r.update(common)
    return r


def clip(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    return text if len(text) <= limit else text[:limit] + "…"


def excerpt_of(section: dict, limit: int) -> str:
    return clip(norm_ws_join(section.get("section_text", "")), limit)


def norm_ws_join(text: str) -> str:
    return _WS.sub(" ", text or "").strip()


def sections_markdown(sections: list[dict], title: str, num_pages: int) -> str:
    lines = [f"# 目录 | {title}", f"> 共 {num_pages} 页 · {len(sections)} 个章节条目", ""]
    for s in sections:
        if s.get("is_title"):
            continue
        indent = "  " * (s["level"] - 1)
        rng = f"第{s['page_start']}页" if s["page_start"] == s["page_end"] \
            else f"第{s['page_start']}-{s['page_end']}页"
        lines.append(f"{indent}- {s['title']}({rng})")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# 查询匹配
# --------------------------------------------------------------------------
def rank_heading_matches(query: str, sections: list[dict]) -> list[dict]:
    q = norm_text(query)
    if not q:
        return []
    scored: list[tuple[int, int, dict]] = []
    for s in sections:
        t = norm_text(s["title"])
        p = norm_text(s["path"])
        score = 0
        if t == q:
            score = 3
        elif t.startswith(q) or q in t:
            score = 2
        elif q in p:
            score = 1
        if score:
            scored.append((-score, len(t), s))
    scored.sort(key=lambda x: (x[0], x[1]))
    return [s for _, _, s in scored]


def search_pages(query: str, structure: dict) -> list[dict]:
    """章节标题未命中时,按页全文检索(去空白后子串匹配,再退化为全词块匹配)。"""
    q = norm_text(query)
    toks = [norm_text(t) for t in tokens_of(query)]
    toks = [t for t in toks if t]
    hits: list[tuple[int, int]] = []  # (命中分, 页码)
    for p, text in structure["page_texts"].items():
        blob = norm_text(text)
        if not blob:
            continue
        if q and q in blob:
            hits.append((3, int(p)))
        elif toks and all(t in blob for t in toks):
            hits.append((1, int(p)))
    hits.sort(key=lambda x: (-x[0], x[1]))
    return [{"page": p, "score": sc} for sc, p in hits]


def sections_on_page(page: int, sections: list[dict]) -> list[dict]:
    return [s for s in sections
            if s["page_start"] <= page <= s["page_end"] and not s.get("is_title")]


def deepest_section_on_page(page: int, sections: list[dict]) -> dict | None:
    cands = sections_on_page(page, sections)
    return max(cands, key=lambda s: s["level"]) if cands else None


def excerpt_around(query: str, page_text: str, limit: int) -> str:
    blob = norm_ws_join(page_text)
    q = norm_ws_join(query)
    idx = blob.find(q)
    if idx < 0:
        for tok in tokens_of(query):
            idx = blob.find(tok)
            if idx >= 0:
                break
    if idx < 0:
        return clip(blob, limit)
    start = max(0, idx - 60)
    return ("…" if start > 0 else "") + clip(blob[start:start + limit], limit)


# --------------------------------------------------------------------------
# 子命令
# --------------------------------------------------------------------------
def _summary_for_log(command: str, result: dict) -> str:
    d = result.get("data") or {}
    if command == "parse":
        return f"sections={len(d.get('sections', []))} lists={d.get('lists_total', 0)}"
    if command == "toc":
        return f"sections={len(d.get('sections', []))}"
    if command == "locate":
        return f"matches={len(d.get('matches', []))} mode={d.get('mode', '')}"
    if command == "open":
        return f"url_generated={'url' in d} launched={d.get('launched', False)}"
    return ""


def cmd_parse(args, pdf: Path) -> dict:
    structure, cache = parse_pdf(pdf, use_cache=not args.no_cache)
    sections = build_sections(structure)
    lists_total = len(structure["list_items"])
    data = {
        "title": structure["title"],
        "page_count": structure["num_pages"],
        "sections": [
            {k: s[k] for k in ("seq", "title", "level", "path",
                               "page_start", "page_end", "list_count")}
            for s in sections
        ],
        "lists_total": lists_total,
    }
    if args.list_items:
        data["list_items"] = [
            {"page": it["page"], "text": clip(norm_ws_join(it["text"]), 120)}
            for it in structure["list_items"]
        ]
    return make_result("ok", f"解析完成:{len(sections)} 个章节条目,{lists_total} 个列表项。",
                       data, pdf=str(pdf), cache=cache)


def cmd_toc(args, pdf: Path) -> dict:
    structure, cache = parse_pdf(pdf, use_cache=not args.no_cache)
    sections = build_sections(structure)
    if args.json:
        data = {
            "title": structure["title"],
            "page_count": structure["num_pages"],
            "sections": [
                {k: s[k] for k in ("seq", "title", "level", "path",
                                   "page_start", "page_end")}
                for s in sections
            ],
            "toc_markdown": sections_markdown(sections, structure["title"],
                                              structure["num_pages"]),
        }
        return make_result("ok", f"目录生成完成:{len(sections)} 个条目。",
                           data, pdf=str(pdf), cache=cache)
    # 默认直接输出 Markdown 目录
    md = sections_markdown(sections, structure["title"], structure["num_pages"])
    return make_result("ok", "目录生成完成。", {"toc_markdown": md},
                       pdf=str(pdf), cache=cache)


def cmd_locate(args, pdf: Path) -> dict:
    structure, cache = parse_pdf(pdf, use_cache=not args.no_cache)
    sections = build_sections(structure)
    usable = [s for s in sections if not s.get("is_title")] or sections

    if args.page is not None:
        page = args.page
        if page < 1 or page > structure["num_pages"]:
            return make_result("no_match",
                               f"页码 {page} 超出范围:该文档共 {structure['num_pages']} 页(1~{structure['num_pages']})。",
                               {"page": page, "page_count": structure["num_pages"]},
                               pdf=str(pdf), cache=cache)
        sec = deepest_section_on_page(page, usable)
        page_text = structure["page_texts"].get(str(page), "")
        data = {
            "page": page,
            "sections_on_page": [
                {k: s[k] for k in ("title", "level", "path")} for s in sections_on_page(page, usable)
            ],
            "text": clip(norm_ws_join(page_text), args.max_chars),
            "truncated": len(norm_ws_join(page_text)) > args.max_chars,
        }
        msg = f"第 {page} 页" + (f"位于「{sec['path']}」" if sec else "")
        return make_result("ok", msg, data, pdf=str(pdf), cache=cache)

    # --query 模式
    matches = rank_heading_matches(args.query, usable)[: args.max_matches]
    if matches:
        data = {
            "query": args.query,
            "match_type": "section",
            "matches": [
                {
                    "title": s["title"],
                    "level": s["level"],
                    "path": s["path"],
                    "page_start": s["page_start"],
                    "page_end": s["page_end"],
                    "excerpt": excerpt_of(s, args.excerpt_chars),
                    "list_count": s["list_count"],
                }
                for s in matches
            ],
        }
        top = matches[0]
        msg = f"「{args.query}」最可能位于「{top['path']}」第 {top['page_start']}" \
              + (f"-{top['page_end']}" if top["page_end"] > top["page_start"] else "") + " 页"
        return make_result("ok", msg, data, pdf=str(pdf), cache=cache)

    # 标题未命中 → 全文检索
    page_hits = search_pages(args.query, structure)[: args.max_matches]
    if page_hits:
        data = {
            "query": args.query,
            "match_type": "content",
            "matches": [],
        }
        for hit in page_hits:
            sec = deepest_section_on_page(hit["page"], usable)
            data["matches"].append({
                "page": hit["page"],
                "score": hit["score"],
                "section_path": sec["path"] if sec else "(无章节标题)",
                "excerpt": excerpt_around(args.query,
                                          structure["page_texts"].get(str(hit["page"]), ""),
                                          args.excerpt_chars),
            })
        msg = f"未命中章节标题,但正文第 {page_hits[0]['page']} 页出现相关内容。"
        return make_result("ok", msg, data, pdf=str(pdf), cache=cache)

    return make_result("no_match",
                       f"未找到与「{args.query}」相关的章节或内容。",
                       {"query": args.query}, pdf=str(pdf), cache=cache)


def _launch_browser_with_url(url: str) -> bool:
    """在 Windows 上用 Chrome/Edge 直接打开 file:///...#page=N。

    背景:本机 file: 协议默认处理程序解析 file:/// URL 不可靠,会报
    「Windows 找不到文件」,因此不走 cmd /c start,而是直接定位浏览器可执行文件。
    返回是否成功启动。
    """
    cands = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    ]
    for exe in cands:
        if not os.path.isfile(exe):
            continue
        try:
            subprocess.Popen([exe, url], close_fds=True)
            return True
        except OSError:
            continue
    return False


def cmd_open(args, pdf: Path) -> dict:
    structure, cache = parse_pdf(pdf, use_cache=not args.no_cache)
    sections = build_sections(structure)

    if args.page is not None:
        page = args.page
        if page < 1 or page > structure["num_pages"]:
            return make_result("no_match",
                               f"页码 {page} 超出范围:该文档共 {structure['num_pages']} 页。",
                               {"page": page}, pdf=str(pdf), cache=cache)
        where = f"第 {page} 页"
    else:
        matches = rank_heading_matches(args.section, sections)
        if not matches:
            return make_result("no_match", f"未找到章节「{args.section}」。",
                               {"section": args.section}, pdf=str(pdf), cache=cache)
        page = matches[0]["page_start"]
        where = f"「{matches[0]['path']}」(第 {page} 页)"

    abs_path = pdf.resolve()
    url = abs_path.as_uri() + f"#page={page}"
    data = {"page": page, "target": where, "url": url, "local_path": str(abs_path),
            "launched": False}

    if args.launch:
        launch_note = ""
        try:
            if sys.platform == "win32":
                # 优先用 Chrome/Edge 打开(支持 #page 跳页,且绕开不可靠的 file: 协议处理程序)
                if _launch_browser_with_url(url):
                    data["launched"] = True
                    launch_note = "(浏览器)"
                else:
                    # 找不到浏览器则用系统默认阅读器打开本地文件(无法定位到具体页)
                    os.startfile(str(abs_path))
                    data["launched"] = True
                    data["page_jump"] = False
                    launch_note = "(默认阅读器,未能定位到具体页)"
            else:
                webbrowser.open(url)
                data["launched"] = True
            msg = f"已在系统阅读器中打开{where}{launch_note}。"
        except OSError as exc:
            # 启动失败时给出本地路径与页码,保证用户仍能手动打开
            data["launch_failed"] = True
            msg = (f"自动打开失败({exc})。请手动打开文件后翻到{where}:"
                   f"{abs_path}")
            return make_result("error", msg, data, pdf=str(pdf), cache=cache)
    else:
        msg = (f"已生成跳转链接:{where}。请点击该链接打开;若链接无法打开,"
               f"请手动打开文件并翻到对应页:{abs_path}")
    return make_result("ok", msg, data, pdf=str(pdf), cache=cache)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc_navigator",
        description="AI 文档结构导航:解析文本型 PDF 结构、生成目录、定位章节/页面、跳转打开。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("pdf", help="PDF 文件路径")
        p.add_argument("--no-cache", action="store_true", help="禁用解析结果缓存")

    p = sub.add_parser("parse", help="解析文档结构(标题/章节树/列表)")
    common(p)
    p.add_argument("--list-items", action="store_true", help="同时输出列表项明细")

    p = sub.add_parser("toc", help="生成目录(默认 Markdown)")
    common(p)
    p.add_argument("--json", action="store_true", help="以 JSON 输出结构化目录")

    p = sub.add_parser("locate", help="定位章节或页面")
    common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--query", help="按章节名/关键词定位")
    g.add_argument("--page", type=int, help="按页码定位,返回该页内容概要")
    p.add_argument("--excerpt-chars", type=int, default=300, help="摘录最大字符数(默认 300)")
    p.add_argument("--max-chars", type=int, default=800, help="--page 模式文本最大字符数(默认 800)")
    p.add_argument("--max-matches", type=int, default=5, help="最多返回的匹配数(默认 5)")

    p = sub.add_parser("open", help="生成跳转链接(--launch 实际打开阅读器)")
    common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", type=int, help="跳转到指定页")
    g.add_argument("--section", help="跳转到指定章节(按标题匹配)")
    p.add_argument("--launch", action="store_true",
                   help="实际调用系统默认阅读器打开(建议先征得用户确认)")

    return parser


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass

    if sys.version_info < (3, 10):
        print(out_json(make_result(
            "error",
            f"Python 版本过低({sys.version_info.major}.{sys.version_info.minor}):"
            "doc-navigator 需要 Python ≥ 3.10。请用系统较新的 Python 运行 "
            "scripts/setup_env.py 完成初始化,再使用 skill 自带的 .venv 解释器。",
            {"interpreter": sys.executable},
        )))
        return EXIT_PARSE

    args = build_parser().parse_args(argv)
    pdf = Path(args.pdf)

    if not pdf.exists():
        result = make_result("error", f"文件不存在:{pdf}", {"pdf": str(pdf)})
        print(out_json(result))
        log_line(args.command, str(pdf), 0.0, "error", "file not found")
        return EXIT_FILE
    if pdf.suffix.lower() != ".pdf":
        result = make_result("error", f"不是 PDF 文件:{pdf.name}", {"pdf": str(pdf)})
        print(out_json(result))
        log_line(args.command, str(pdf), 0.0, "error", "not a pdf")
        return EXIT_FILE

    start = time.perf_counter()
    try:
        if args.command == "parse":
            result = cmd_parse(args, pdf)
        elif args.command == "toc":
            result = cmd_toc(args, pdf)
        elif args.command == "locate":
            result = cmd_locate(args, pdf)
        elif args.command == "open":
            result = cmd_open(args, pdf)
        else:  # pragma: no cover
            result = make_result("error", f"未知命令:{args.command}")
    except Exception as exc:  # 解析失败等运行期异常统一兜底
        result = make_result("error", f"解析失败:{exc.__class__.__name__}: {exc}",
                             {"pdf": str(pdf)})

    elapsed = time.perf_counter() - start
    result.setdefault("pdf", str(pdf))
    result["elapsed_s"] = round(elapsed, 2)
    print(out_json(result))
    log_line(args.command, str(pdf), elapsed, result["status"],
             _summary_for_log(args.command, result))

    if result["status"] == "ok":
        return EXIT_OK
    if result["status"] == "no_match":
        return EXIT_NO_MATCH
    return EXIT_PARSE


if __name__ == "__main__":
    sys.exit(main())
