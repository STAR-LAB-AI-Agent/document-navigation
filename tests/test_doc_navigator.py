# -*- coding: utf-8 -*-
"""doc_navigator CLI 的验收测试:覆盖正常输入、边界情况与异常情况。

测试通过子进程调用 CLI(与 nanobot 实际调用方式一致),
依赖 test_data/ 下的两个 PDF(任务书 + 生成的样例报告)。
首次运行会触发完整解析(约 1 分钟),之后走缓存很快。
"""
import json
import socket
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from make_sample_pdf import ensure_sample_pdf

PROJECT_ROOT = Path(__file__).resolve().parent.parent
VENV_PY = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
SCRIPT = PROJECT_ROOT / "scripts" / "doc_navigator.py"
SETUP_SCRIPT = PROJECT_ROOT / "scripts" / "setup_env.py"
TASKBOOK = PROJECT_ROOT / "test_data" / "智能体开发实战_课程实验任务书.pdf"


def run_cli(*args: str):
    """执行 CLI,返回 (退出码, 解析后的 JSON, 原始 stdout)。"""
    proc = subprocess.run(
        [str(VENV_PY), str(SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
    )
    stdout = proc.stdout.strip()
    payload = json.loads(stdout) if stdout else {}
    return proc.returncode, payload, stdout


def setup_module(module):
    ensure_sample_pdf()  # 样例 PDF 不存在则生成


# --------------------------------------------------------------------------
# 正常输入
# --------------------------------------------------------------------------
def test_01_parse_taskbook_structure():
    code, payload, _ = run_cli("parse", str(TASKBOOK))
    assert code == 0
    assert payload["status"] == "ok"
    assert "课程实验任务书" in payload["data"]["title"]
    assert payload["data"]["page_count"] == 7
    titles = [s["title"] for s in payload["data"]["sections"]]
    assert any("实验内容" in t for t in titles)
    assert payload["data"]["lists_total"] > 10  # 任务书含大量列表项
    assert all(s["page_start"] >= 1 for s in payload["data"]["sections"])


def test_02_toc_markdown():
    """默认输出 Markdown 表格:每行一个条目、页码独立成列,层级靠 └ 前缀。

    这是为聊天前端做的设计——相邻行即使被合并,表格行与层级前缀依然可读。
    """
    code, payload, _ = run_cli("toc", str(TASKBOOK))
    assert code == 0
    md = payload["data"]["toc_markdown"]
    assert "一、实验目的" in md
    assert "七、提交材料" in md
    assert "| 章节 | 页码 |" in md            # 表格表头
    assert "| --- | --- |" in md
    assert "| 　└4.1 基本技术要求 | 第5页 |" in md  # 子章节带层级前缀
    assert "第2-4页" in md                     # 跨页区间


def test_02b_toc_list_style_opt_in():
    """--list 保留嵌套列表形态(终端/标准渲染器)。"""
    code, payload, _ = run_cli("toc", str(TASKBOOK), "--list")
    assert code == 0
    md = payload["data"]["toc_markdown"]
    assert "\n  - 4.1 基本技术要求(" in md      # 子章节缩进两格
    assert "- 四、总体要求(" in md            # 父章节无缩进
    assert "| 章节 | 页码 |" not in md          # 不是表格形态


def test_03_toc_json_levels():
    code, payload, _ = run_cli("toc", str(TASKBOOK), "--json")
    assert code == 0
    sections = payload["data"]["sections"]
    by_title = {s["title"]: s for s in sections}
    assert by_title["四、总体要求"]["level"] == 1
    assert by_title["4.1 基本技术要求"]["level"] == 2
    assert by_title["4.1 基本技术要求"]["path"].startswith("四、总体要求")


def test_04_locate_section_hit():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--query", "评分标准")
    assert code == 0
    assert payload["status"] == "ok"
    assert payload["data"]["match_type"] == "section"
    top = payload["data"]["matches"][0]
    assert "评分标准" in top["title"]
    assert 6 <= top["page_start"] <= 7
    assert top["excerpt"]  # 附带内容摘录


def test_05_locate_content_fallback():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--query", "文档结构导航")
    assert code == 0
    data = payload["data"]
    assert data["match_type"] == "content"
    assert data["matches"][0]["page"] == 3
    assert "文档结构导航" in data["matches"][0]["excerpt"]


def test_06_locate_by_page():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--page", "3")
    assert code == 0
    data = payload["data"]
    assert data["page"] == 3
    assert any("实验内容" in s["title"] for s in data["sections_on_page"])
    assert len(data["text"]) > 0


def test_07_link_generates_url():
    code, payload, _ = run_cli("link", str(TASKBOOK), "--page", "7", "--file-url")
    assert code == 0
    data = payload["data"]
    assert data["url"].endswith("#page=7")
    assert data["url_scheme"] == "file"
    assert "第 7 页" in data["target"]


def test_07b_link_by_section():
    code, payload, _ = run_cli("link", str(TASKBOOK), "--section", "评分标准", "--file-url")
    assert code == 0
    data = payload["data"]
    assert data["url"].endswith("#page=" + str(data["page"]))
    assert "评分标准" in data["target"]


def test_07c_toc_with_links():
    """链接只加在章节标题上:页码列是纯文本,避免同一 URL 每行重复两次。"""
    code, payload, _ = run_cli("toc", str(TASKBOOK), "--links", "--file-url")
    assert code == 0
    md = payload["data"]["toc_markdown"]
    assert "](file:///" in md  # 章节标题为可点击链接
    assert "#page=" in md
    assert "一、实验目的" in md
    rows = [l for l in md.splitlines() if l.startswith("|") and not l.startswith("| 章节")
            and not l.startswith("| ---")]
    assert rows, "目录表格应有数据行"
    assert all(l.count("](file:///") == 1 for l in rows), \
        "每行只应有一个链接(页码列不应重复同一个 URL)"
    assert any(" | 第" in l for l in rows), "页码列应为纯文本"


def test_07c2_toc_list_style_links_not_duplicated():
    """--list --links 同样只在标题上带链接,页码为纯文本。"""
    code, payload, _ = run_cli("toc", str(TASKBOOK), "--list", "--links", "--file-url")
    assert code == 0
    md = payload["data"]["toc_markdown"]
    items = [l for l in md.splitlines() if l.lstrip().startswith("- ")]
    assert items, "列表形态应有条目"
    assert all(l.count("](file:///") == 1 for l in items), \
        "每个列表项只应有一个链接"
    assert any("(第" in l for l in items), "页码应以纯文本括号形式出现"


def _http_get(url: str, max_bytes: int = 256) -> bytes:
    with urllib.request.urlopen(url, timeout=5) as r:
        return r.read(max_bytes)


def test_07d_link_http_clickable():
    """link 默认生成 http 链接:内嵌服务应可 ping、可取回 PDF、其余路径 404。"""
    try:
        code, payload, _ = run_cli("link", str(TASKBOOK), "--page", "7")
        assert code == 0
        data = payload["data"]
        url = data["url"]
        assert url.startswith("http://127.0.0.1:")
        assert url.endswith("#page=7")
        assert data["url_scheme"] == "http"
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        assert _http_get(origin + "/doc-navigator-ping") == b"doc-navigator serve ok"
        head = _http_get(url)  # 文件本身可取回
        assert head[:5] == b"%PDF-"
        try:
            urllib.request.urlopen(origin + "/not-the-pdf.txt", timeout=5)
            raise AssertionError("unexpected 200")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        # 结束所有内嵌 HTTP 服务,避免残留进程
        run_cli("_serve-stop")


def test_07e_link_reuses_one_daemon():
    """同一 PDF 连续生成链接应复用同一个内嵌服务,不重复占端口。"""
    run_cli("_serve-stop")
    try:
        code1, p1, _ = run_cli("link", str(TASKBOOK), "--page", "3")
        code2, p2, _ = run_cli("link", str(TASKBOOK), "--page", "5")
        assert code1 == 0 and code2 == 0
        assert p1["data"]["url_scheme"] == "http"
        assert p1["data"]["http_server"]["port"] == p2["data"]["http_server"]["port"]
        state = json.loads(
            (PROJECT_ROOT / ".cache" / "serve_state.json").read_text(encoding="utf-8"))
        assert len(state) == 1  # 只应有一个服务进程
    finally:
        run_cli("_serve-stop")


def test_07f_links_degrade_when_ports_busy():
    """内嵌服务端口全被占用时,link/toc --links 应退回 file:/// 链接而不是整体失败。"""
    run_cli("_serve-stop")
    held = []
    try:
        for port in range(8787, 8798):
            s = socket.socket()
            try:
                s.bind(("127.0.0.1", port))
                s.listen(1)
                held.append(s)
            except OSError:
                s.close()  # 已被别的进程占用,该端口同样不可用
        code, payload, _ = run_cli("link", str(TASKBOOK), "--page", "7")
        assert code == 0
        assert payload["status"] == "ok"
        assert payload["data"]["url_scheme"] == "file"
        assert payload["data"]["url"].startswith("file:///")
        assert "error" in payload["data"]["http_server"]  # 如实记录失败原因
        assert "file:///" in payload["message"]            # 如实告知用户链接形式

        code, payload, _ = run_cli("toc", str(TASKBOOK), "--links")
        assert code == 0
        assert payload["data"]["url_scheme"] == "file"
        assert "](file:///" in payload["data"]["toc_markdown"]
    finally:
        for s in held:
            s.close()
        run_cli("_serve-stop")


def test_07g_toc_file_url_implies_links():
    """toc --file-url 不带 --links 时,也应生成可点击的 file:/// 目录。"""
    code, payload, _ = run_cli("toc", str(TASKBOOK), "--file-url")
    assert code == 0
    assert payload["data"]["url_scheme"] == "file"
    assert "](file:///" in payload["data"]["toc_markdown"]


def test_07h_log_records_toc_section_count():
    """toc 不带 --json 时日志也要记真实条目数(曾经因缺 sections 键记成 0)。"""
    log = PROJECT_ROOT / "logs" / "doc_navigator.log"
    run_cli("toc", str(TASKBOOK))
    lines = [l for l in log.read_text(encoding="utf-8").splitlines()
             if "| toc " in l and TASKBOOK.name in l]
    assert lines, "未找到 toc 的运行日志"
    # 12 个章节条目含文档标题,与 message 中"12 个条目"口径一致
    assert lines[-1].rstrip().endswith("sections=12")

# --------------------------------------------------------------------------
# 边界情况与异常情况
# --------------------------------------------------------------------------
def test_08_locate_no_match():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--query", "量子纠缠暗物质")
    assert code == 2
    assert payload["status"] == "no_match"


def test_09b_link_page_out_of_range():
    code, payload, _ = run_cli("link", str(TASKBOOK), "--page", "999")
    assert code == 2
    assert payload["status"] == "no_match"
    assert "超出范围" in payload["message"]


def test_09c_locate_page_out_of_range():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--page", "999")
    assert code == 2
    assert payload["status"] == "no_match"
    assert "超出范围" in payload["message"]


def test_10_file_not_found():
    code, payload, _ = run_cli("parse", "不存在的文件.pdf")
    assert code == 3
    assert payload["status"] == "error"
    assert "不存在" in payload["message"]


def test_11_not_a_pdf():
    txt = PROJECT_ROOT / "test_data" / "not_a_pdf.txt"
    txt.write_text("这不是 PDF", encoding="utf-8")
    try:
        code, payload, _ = run_cli("parse", str(txt))
        assert code == 3
        assert payload["status"] == "error"
        assert "PDF" in payload["message"]
    finally:
        txt.unlink(missing_ok=True)


def test_12_sample_pdf_structure():
    sample = ensure_sample_pdf()
    code, payload, _ = run_cli("parse", str(sample))
    assert code == 0
    data = payload["data"]
    assert "样例技术报告" in data["title"]
    titles = [s["title"] for s in data["sections"]]
    assert any("第二章" in t for t in titles)
    levels = {s["title"]: s["level"] for s in data["sections"]}
    sub = [t for t in levels if t.startswith("1.")]
    assert sub, "应识别出 1.x 编号的二级标题"
    assert all(levels[t] == 2 for t in sub)


def test_13_setup_env_check_reports_ready():
    """setup_env.py --check:项目 venv 解释器应报告环境就绪(退出码 0)。"""
    proc = subprocess.run(
        [str(VENV_PY), str(SETUP_SCRIPT), "--check"],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
    )
    assert proc.returncode == 0
    assert proc.stdout.strip().startswith("ready:")


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-v"]))
