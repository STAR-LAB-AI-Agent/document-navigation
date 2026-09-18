#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
doc-navigator:AI 文档结构导航(实验第 10 题)
================================================

基于 Docling 解析文本型 PDF 的标题、章节和列表结构,生成目录与层级化章节导航,
并根据自然语言指令定位目标章节或页面。"跳转"通过生成可点击的章节/页码链接实现——
依靠目录结果即可定位到目标章节,不调用任何 Docling 以外的外部工具去切换页面。

为了让链接在 webui 与终端里都真正可点(浏览器禁止 http 页面点击 file:// 链接,
终端裸 URL 文本不可点击),链接默认经由 skill 内嵌的本地 HTTP 服务提供:
纯 Python 标准库实现,只绑定 127.0.0.1、只服务目标 PDF 一个文件,由脚本自身
以分离进程启动,空闲 15 分钟自动退出;全程不打开任何阅读器/浏览器。端口耗尽等
导致服务起不来时,不整体失败——自动退回 file:/// 链接并在 message 中如实说明。

子命令:
    parse   <pdf>                  解析文档结构(标题/章节树/列表)
    toc     <pdf> [--json] [--links]  生成目录(默认 Markdown,--links 可点击)
    locate  <pdf> --query "..."/--page N   定位章节或页面
    link    <pdf> --page N/--section "..." 生成对应页面/章节的可点击跳转链接
    (_serve-daemon / _serve-stop 为内嵌 HTTP 服务的内部管理命令,不面向用户)

统一输出 JSON(status/message/data),退出码:0 成功 2 未找到 3 文件错误 4 解析/执行失败。
页码一律为 PDF 物理页码(从 1 开始)。
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from collections import OrderedDict
from collections.abc import Callable
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
# 跳转链接生成 + 内嵌本地 HTTP 服务
# --------------------------------------------------------------------------
# 背景:webui 页面里的 file:// 链接会被浏览器安全策略拦截(http 页面禁止跳转
# file: 协议),终端里裸 URL 文本也不可点击。因此跳转链接默认经由本 skill
# 内嵌的本地 HTTP 服务(纯 Python 标准库实现,绑定 127.0.0.1,不调用任何
# 外部程序、不打开阅读器,空闲自动退出)提供,生成 http://127.0.0.1:PORT/
# <文件名>#page=N 链接——webui 和终端里的 Markdown 链接均可点击,浏览器
# 自带 PDF 查看器支持 #page=N 定位到具体页。

_SERVE_PORT_START = 8787
_SERVE_PORT_END = 8797
_SERVE_PING_PATH = "/doc-navigator-ping"
_SERVE_IDLE_DEFAULT = 900  # 秒:无任何请求时守护进程自动退出


class ServeError(RuntimeError):
    """内嵌 HTTP 服务不可用(端口耗尽或启动失败)。

    调用方不应因此整体失败:http 链接只是让链接更易点击的手段,退回 file:/// 链接
    仍可完成"生成跳转链接"这件事,所以 link/toc 捕获本异常后走兜底方案。
    """


def page_link(pdf_path: Path, page: int) -> str:
    """生成指向 PDF 指定页的 file:// 链接(#page=N 片段为 PDF 标准跳页语法,
    多数浏览器/阅读器点击后直接定位到该页)。作为 http 链接不可用时的兜底。"""
    return pdf_path.resolve().as_uri() + f"#page={page}"


def http_page_link(port: int, pdf_path: Path, page: int) -> str:
    """生成经由内嵌 HTTP 服务的跳转链接(浏览器/终端均可点击)。"""
    name = urllib.parse.quote(pdf_path.name)
    return f"http://127.0.0.1:{port}/{name}#page={page}"


def md_link(text: str, url: str) -> str:
    """转义 Markdown 链接文本中的方括号,生成 [文本](链接)。"""
    safe = text.replace("[", "\\[").replace("]", "\\]")
    return f"[{safe}]({url})"


def _serve_state_file() -> Path:
    return CACHE_DIR / "serve_state.json"


def _load_serve_state() -> list[dict]:
    try:
        with open(_serve_state_file(), encoding="utf-8") as f:
            return [e for e in json.load(f) if isinstance(e, dict)]
    except (OSError, ValueError):
        return []


def _save_serve_state(entries: list[dict]) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(_serve_state_file(), "w", encoding="utf-8") as f:
            json.dump(entries, f, ensure_ascii=False)
    except OSError:
        pass  # 状态文件写失败只影响复用,不影响本次链接


def _ping_serve(port: int) -> bool:
    """探测端口上是否运行着本 skill 的 HTTP 服务。"""
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}{_SERVE_PING_PATH}", timeout=1.5) as r:
            return r.status == 200 and b"doc-navigator" in r.read(64)
    except OSError:
        return False


def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _spawn_serve_daemon(port: int, pdf: Path, idle: int) -> int:
    """以分离进程启动内嵌 HTTP 服务(仅服务这一个 PDF 文件),返回其 pid。"""
    cmd = [sys.executable, str(Path(__file__).resolve()), "_serve-daemon",
           "--port", str(port), "--root", str(pdf.resolve().parent),
           "--name", pdf.name, "--idle-timeout", str(idle)]
    kwargs: dict = {"cwd": str(SKILL_ROOT),
                    "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(cmd, close_fds=True, **kwargs).pid


def ensure_http_serve(pdf: Path, idle: int = _SERVE_IDLE_DEFAULT) -> int:
    """确保存在一个正在服务该 PDF 的内嵌 HTTP 服务,返回其端口。

    已有服务直接复用;否则从未占用端口中挑选一个启动守护进程并等待就绪。
    端口耗尽或启动失败时抛 ServeError,由调用方退回 file:/// 链接。
    """
    root = str(pdf.resolve().parent)
    name = pdf.name
    state = [e for e in _load_serve_state() if _ping_serve(int(e["port"]))]
    for e in state:
        if e.get("root") == root and e.get("name") == name:
            _save_serve_state(state)
            return int(e["port"])
    used = {int(e["port"]) for e in state}
    for port in range(_SERVE_PORT_START, _SERVE_PORT_END + 1):
        if port in used or not _port_free(port):
            continue
        try:
            pid = _spawn_serve_daemon(port, pdf, idle)
        except OSError as exc:
            raise ServeError(f"内嵌 HTTP 服务进程启动失败:{exc}") from exc
        for _ in range(10):  # 最多等 ~5s 就绪
            if _ping_serve(port):
                state.append({"port": port, "root": root, "name": name,
                              "pid": pid, "started": int(time.time())})
                _save_serve_state(state)
                return port
            time.sleep(0.5)
    raise ServeError(
        f"端口 {_SERVE_PORT_START}~{_SERVE_PORT_END} 均被占用,无法启动内嵌 HTTP 服务")


def _serve_stop_all() -> None:
    """终止所有本 skill 启动的 HTTP 守护进程并清空状态。"""
    entries = _load_serve_state()
    for e in entries:
        port = int(e.get("port", 0))
        if _ping_serve(port):
            try:
                if sys.platform == "win32":
                    subprocess.run(["taskkill", "/F", "/PID", str(e["pid"])],
                                   capture_output=True, timeout=10)
                elif e.get("pid"):
                    os.kill(int(e["pid"]), 15)
            except (OSError, subprocess.SubprocessError):
                pass
    _save_serve_state([])


class _SingleFileHandler(http.server.BaseHTTPRequestHandler):
    """只服务启动时指定的那一个 PDF 文件;其余路径一律 404。"""

    server_version = "doc-navigator-serve/1"

    def _emit(self, code: int, ctype: str, body: bytes = b"") -> None:
        self.send_response(code)
        if ctype:
            self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_target(self) -> None:
        rec = getattr(self.server, "serve_record", {})
        on_hit = getattr(self.server, "on_request", None)
        if on_hit:
            on_hit()

        path = urllib.parse.unquote(urllib.parse.urlparse(self.path).path)
        if path == _SERVE_PING_PATH:
            self._emit(200, "text/plain; charset=utf-8",
                       "doc-navigator serve ok".encode("utf-8"))
            return
        target = "/" + rec.get("name", "")
        if sys.platform == "win32":
            path, target = path.lower(), target.lower()
        if path != target:
            self._emit(404, "text/plain; charset=utf-8", b"not found")
            return
        pdf = Path(rec.get("root", "")) / rec.get("name", "")
        try:
            with open(pdf, "rb") as f:
                body = f.read()
        except OSError:
            self._emit(404, "text/plain; charset=utf-8", b"file unavailable")
            return
        fname = urllib.parse.quote(pdf.name)
        self.send_response(200)
        self.send_header("Content-Type", "application/pdf")
        self.send_header("Content-Disposition",
                         f"inline; filename*=UTF-8''{fname}")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = _serve_target
    do_HEAD = _serve_target

    def log_message(self, fmt, *args):  # 静默访问日志
        pass


def run_serve_daemon(args) -> int:
    """内嵌 HTTP 服务主循环:启动即服务,空闲 idle 秒后自动退出。"""
    root = Path(args.root)
    pdf = root / args.name
    if not pdf.is_file():
        print(out_json(make_result("error", f"服务目标不存在:{pdf}")))
        return EXIT_FILE

    last_hit = {"t": time.time()}

    class Handler(_SingleFileHandler):
        pass

    try:
        server = http.server.ThreadingHTTPServer(
            ("127.0.0.1", args.port), Handler)
    except OSError as exc:
        print(out_json(make_result("error", f"端口 {args.port} 绑定失败:{exc}")))
        return EXIT_PARSE

    server.serve_record = {"root": str(root), "name": args.name}
    server.on_request = lambda: last_hit.update(t=time.time())

    t = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.5},
                         daemon=True)
    t.start()
    while True:
        time.sleep(0.5)
        if time.time() - last_hit["t"] > args.idle_timeout:
            break
    server.shutdown()
    return EXIT_OK
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


def tree_prefix(level: int) -> str:
    """层级可视前缀:1 级无前缀,子级用 └ 加缩进递进。

    层级全靠前缀字符表达,不依赖行首缩进;这样即便渲染器把相邻行合并成
    一行(markdown 里子项会因此变成父项段落的一部分),层级依然看得见。
    """
    return "" if level <= 1 else "　" * (level - 1) + "└"


def page_range_text(s: dict) -> str:
    return f"第{s['page_start']}页" if s["page_start"] == s["page_end"] \
        else f"第{s['page_start']}-{s['page_end']}页"


def sections_markdown(sections: list[dict], title: str, num_pages: int,
                      link_for=None, style: str = "table") -> str:
    """生成 Markdown 目录。

    style="table"(默认):每条目一行的表格。表格行的层级由第一列的前缀字符
    表达,不依赖行首缩进/Markdown 嵌套列表——部分聊天前端(如 nanobot webui)
    会把助手消息中相邻的行合并成一行,嵌套列表在那样的渲染下会挤成一坨;
    表格每行自带 `|` 分隔,即便也被合并,层级前缀与页码列依然可读。

    style="list":嵌套 Markdown 列表(终端/标准渲染器下的经典形式)。

    传入 link_for(页码 → 链接)时,只有章节标题带链接,页码为纯文本:标题与页码
    本就指向同一个 URL,两处都写成链接会让 URL 在长目录里重复一遍、token 近乎
    翻倍;而聊天前端对单条消息有输出上限(如 nanobot 默认 8192 tokens),超限后
    消息从中间被截断,续写部分没有表头,表格后半段就无法渲染成表格。
    """
    shown = [s for s in sections if not s.get("is_title")]
    head = [f"# 目录 | {title}", f"> 共 {num_pages} 页 · {len(sections)} 个章节条目", ""]
    lines: list[str] = []
    if style == "list":
        for s in shown:
            indent = "  " * (s["level"] - 1)
            rng = page_range_text(s)
            if link_for is not None:
                lines.append(f"{indent}- {md_link(s['title'], link_for(s['page_start']))}({rng})")
            else:
                lines.append(f"{indent}- {s['title']}({rng})")
    else:
        lines = ["| 章节 | 页码 |", "| --- | --- |"]
        for s in shown:
            cell = tree_prefix(s["level"]) + s["title"]
            rng = page_range_text(s)
            if link_for is not None:
                lines.append(f"| {md_link(cell, link_for(s['page_start']))} | {rng} |")
            else:
                lines.append(f"| {cell} | {rng} |")
    return "\n".join(head + lines)


def make_link_for(pdf: Path, use_http: bool) -> tuple[Callable[[int], str], str]:
    """返回 (页码 → 链接 的函数, 实际使用的方案 "http"/"file")。

    use_http 时先确保内嵌 HTTP 服务就绪;服务不可用(端口耗尽等)时不报错,
    自动退回 file:/// 链接——目录与跳转链接本身仍然生成得出来。
    """
    if use_http:
        try:
            port = ensure_http_serve(pdf)
        except ServeError:
            pass
        else:
            return (lambda page: http_page_link(port, pdf, page)), "http"
    return (lambda page: page_link(pdf, page)), "file"


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
        # 不带 --json 时 data 里只有 toc_markdown,按目录输出行统计,避免记成 0
        # (与 message 里"12 个条目"口径一致:含文档标题)
        if isinstance(d.get("sections"), list):
            return f"sections={len(d['sections'])}"
        entries = sum(
            1 for line in (d.get("toc_markdown") or "").splitlines()
            if line.lstrip().startswith("- ")                       # 列表形态
            or (line.startswith("| ") and "---" not in line)        # 表格形态(含表头)
        )
        return f"sections={entries}"
    if command == "locate":
        return f"matches={len(d.get('matches', []))} mode={d.get('mode', '')}"
    if command == "link":
        return f"url_scheme={d.get('url_scheme', '')} url={'url' in d}"
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

    link_for, scheme, note = None, None, ""
    if args.links or args.file_url:  # --file-url 隐含 --links
        link_for, scheme = make_link_for(pdf, use_http=not args.file_url)
        if scheme == "file" and not args.file_url:
            note = ("内嵌 HTTP 服务不可用,目录中的链接已退回 file:///:"
                    "部分浏览器会拦截 file://,请在终端或本地阅读器中打开。")

    md = sections_markdown(sections, structure["title"], structure["num_pages"], link_for,
                           style="list" if args.list else "table")
    data: dict = {"toc_markdown": md}
    if args.json:
        data = {
            "title": structure["title"],
            "page_count": structure["num_pages"],
            "sections": [
                {k: s[k] for k in ("seq", "title", "level", "path",
                                   "page_start", "page_end")}
                for s in sections
            ],
            "toc_markdown": md,
        }
    if scheme:
        data["url_scheme"] = scheme
    msg = f"目录生成完成:{len(sections)} 个条目。" + (f"注意:{note}" if note else "")
    return make_result("ok", msg, data, pdf=str(pdf), cache=cache)


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


def cmd_link(args, pdf: Path) -> dict:
    """生成对应页面/章节的可点击跳转链接。只生成链接,不打开任何程序——
    "跳转"由用户点击链接完成。默认走内嵌 HTTP 服务(http 链接,webui/终端
    均可点击);--file-url 时退回 file:/// 链接兜底。"""
    structure, cache = parse_pdf(pdf, use_cache=not args.no_cache)
    sections = build_sections(structure)

    if args.page is not None:
        page = args.page
        if page < 1 or page > structure["num_pages"]:
            return make_result("no_match",
                               f"页码 {page} 超出范围:该文档共 {structure['num_pages']} 页。",
                               {"page": page}, pdf=str(pdf), cache=cache)
        sec = deepest_section_on_page(page, [s for s in sections if not s.get("is_title")])
        where = f"第 {page} 页" + (f"(「{sec['path']}」)" if sec else "")
    else:
        matches = rank_heading_matches(args.section, sections)
        if not matches:
            return make_result("no_match", f"未找到章节「{args.section}」。",
                               {"section": args.section}, pdf=str(pdf), cache=cache)
        top = matches[0]
        page = top["page_start"]
        where = f"「{top['path']}」(第 {page} 页)"

    abs_path = pdf.resolve()
    data = {"page": page, "target": where, "local_path": str(abs_path)}
    note = ""
    if args.file_url:
        url = page_link(pdf, page)
        data["url_scheme"] = "file"
    else:
        try:
            port = ensure_http_serve(pdf)
        except ServeError as exc:
            # 内嵌服务不可用时不整体失败:退回 file:/// 链接,并如实说明原因
            url = page_link(pdf, page)
            data["url_scheme"] = "file"
            data["http_server"] = {"error": str(exc), "file_url": url}
            note = (f"内嵌 HTTP 服务不可用({exc}),链接为 file:/// 形式:"
                    "浏览器可能拦截,请在终端 Ctrl+点击或用本地阅读器打开。")
        else:
            url = http_page_link(port, pdf, page)
            data["url_scheme"] = "http"
            data["http_server"] = {"port": port, "serving": pdf.name,
                                   "file_url": page_link(pdf, page)}
    data["url"] = url
    msg = (f"已生成跳转链接:{where}。请以 Markdown 链接形式发给用户点击定位:"
           f"{md_link(where, url)}" + (f"注意:{note}" if note else ""))
    return make_result("ok", msg, data, pdf=str(pdf), cache=cache)


# --------------------------------------------------------------------------
# 入口
# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="doc_navigator",
        description="AI 文档结构导航:解析文本型 PDF 结构、生成目录与层级化章节导航、"
                    "定位章节/页面、生成可点击的章节/页码跳转链接。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p):
        p.add_argument("pdf", help="PDF 文件路径")
        p.add_argument("--no-cache", action="store_true", help="禁用解析结果缓存")

    p = sub.add_parser("parse", help="解析文档结构(标题/章节树/列表)")
    common(p)
    p.add_argument("--list-items", action="store_true", help="同时输出列表项明细")

    p = sub.add_parser("toc", help="生成目录(默认 Markdown 表格;--links 生成可点击目录)")
    common(p)
    p.add_argument("--json", action="store_true", help="以 JSON 输出结构化目录")
    p.add_argument("--list", action="store_true",
                   help="改回嵌套 Markdown 列表(终端更紧凑;聊天前端可能把子项挤进父项同一行)")
    p.add_argument("--links", action="store_true",
                   help="目录中的章节标题带 #page=N 可点击链接(默认经内嵌 HTTP 服务;"
                        "页码为纯文本,避免同一 URL 每行重复、撑爆单条消息的输出上限)")
    p.add_argument("--file-url", action="store_true",
                   help="可点击目录退回 file:/// 链接(隐含 --links;"
                        "内嵌 HTTP 服务不可用时也会自动退回)")

    p = sub.add_parser("locate", help="定位章节或页面")
    common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--query", help="按章节名/关键词定位")
    g.add_argument("--page", type=int, help="按页码定位,返回该页内容概要")
    p.add_argument("--excerpt-chars", type=int, default=300, help="摘录最大字符数(默认 300)")
    p.add_argument("--max-chars", type=int, default=800, help="--page 模式文本最大字符数(默认 800)")
    p.add_argument("--max-matches", type=int, default=5, help="最多返回的匹配数(默认 5)")

    p = sub.add_parser("link", help="生成对应页面/章节的可点击跳转链接(不打开任何程序)")
    common(p)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--page", type=int, help="生成跳转到指定页的链接")
    g.add_argument("--section", help="生成跳转到指定章节(按标题匹配)的链接")
    p.add_argument("--file-url", action="store_true",
                   help="退回 file:/// 链接(默认经内嵌 HTTP 服务生成 http 链接,更易点击)")

    # 隐藏子命令:内嵌 HTTP 服务的管理面,供 link/toc 自动调用,不面向用户
    d = sub.add_parser("_serve-daemon", help=argparse.SUPPRESS)
    d.add_argument("--port", type=int, required=True)
    d.add_argument("--root", required=True)
    d.add_argument("--name", required=True)
    d.add_argument("--idle-timeout", type=int, default=_SERVE_IDLE_DEFAULT)

    sub.add_parser("_serve-stop", help=argparse.SUPPRESS)

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

    if args.command == "_serve-daemon":
        return run_serve_daemon(args)
    if args.command == "_serve-stop":
        _serve_stop_all()
        print(out_json(make_result("ok", "内嵌 HTTP 服务已全部停止。",
                                   {"stopped": True})))
        return EXIT_OK

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
        elif args.command == "link":
            result = cmd_link(args, pdf)
        else:  # pragma: no cover
            result = make_result("error", f"未知命令:{args.command}")
    except ServeError as exc:  # 内嵌 HTTP 服务问题单独提示,不要与解析失败混淆
        result = make_result("error", f"服务启动失败:{exc}", {"pdf": str(pdf)})
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
