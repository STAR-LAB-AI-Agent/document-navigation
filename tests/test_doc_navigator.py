# -*- coding: utf-8 -*-
"""doc_navigator CLI 的验收测试:覆盖正常输入、边界情况与异常情况。

测试通过子进程调用 CLI(与 nanobot 实际调用方式一致),
依赖 test_data/ 下的两个 PDF(任务书 + 生成的样例报告)。
首次运行会触发完整解析(约 1 分钟),之后走缓存很快。
"""
import json
import subprocess
import sys
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
    code, payload, _ = run_cli("toc", str(TASKBOOK))
    assert code == 0
    md = payload["data"]["toc_markdown"]
    assert "一、实验目的" in md
    assert "七、提交材料" in md
    assert "第2页" in md  # 目录带页码


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


def test_07_open_generates_url():
    code, payload, _ = run_cli("open", str(TASKBOOK), "--page", "7")
    assert code == 0
    data = payload["data"]
    assert data["url"].endswith("#page=7")
    assert data["launched"] is False  # 不加 --launch 不应实际打开


# --------------------------------------------------------------------------
# 边界情况与异常情况
# --------------------------------------------------------------------------
def test_08_locate_no_match():
    code, payload, _ = run_cli("locate", str(TASKBOOK), "--query", "量子纠缠暗物质")
    assert code == 2
    assert payload["status"] == "no_match"


def test_09_locate_page_out_of_range():
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
