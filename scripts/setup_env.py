#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""doc-navigator 首次初始化引导脚本。

在任意机器上部署本 skill 后,用系统里任意 Python ≥3.10 运行一次:

    python scripts/setup_env.py

它会在 skill 目录下创建自带的虚拟环境 .venv,并安装 requirements.txt
(docling 等)。之后统一用该 venv 的解释器运行 doc_navigator.py,
不再依赖任何固定路径或系统全局环境。

    python scripts/setup_env.py --check   # 仅检查当前解释器是否已就绪,不做任何修改

退出码:0 就绪/成功;3 当前解释器缺少依赖或版本过低;4 初始化过程失败。
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parent.parent
VENV_DIR = SKILL_ROOT / ".venv"
REQUIREMENTS = SKILL_ROOT / "requirements.txt"
MIN_PY = (3, 10)

EXIT_OK = 0
EXIT_MISSING = 3
EXIT_FAILED = 4


def venv_python(venv: Path) -> Path:
    rel = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    return venv / rel


def run(cmd: list[str]) -> int:
    print(f"[setup_env] $ {' '.join(str(c) for c in cmd)}", flush=True)
    proc = subprocess.run(cmd)
    return proc.returncode


def check_current_interpreter() -> int:
    """检查运行本脚本的解释器是否已具备运行 doc_navigator.py 的条件。"""
    v = sys.version_info
    ver_str = f"{v.major}.{v.minor}.{v.micro}"
    if v < MIN_PY:
        print(f"not-ready: Python {ver_str} 过低,docling 需要 Python ≥ 3.10。", flush=True)
        return EXIT_MISSING
    try:
        import docling  # noqa: F401
    except ImportError as exc:
        print(f"not-ready: 当前解释器(Python {ver_str})未安装 docling({exc})。"
              "运行 `python scripts/setup_env.py` 完成首次安装,或改用 skill 自带的 .venv 解释器。",
              flush=True)
        return EXIT_MISSING
    import docling
    print(f"ready: docling {docling.__version__} @ Python {ver_str} ({sys.executable})", flush=True)
    return EXIT_OK


def setup() -> int:
    v = sys.version_info
    if v < MIN_PY:
        print(f"[setup_env] 当前 Python {v.major}.{v.minor} 过低:"
              f"请用 Python ≥ 3.10 运行本脚本。", flush=True)
        return EXIT_FAILED

    if not REQUIREMENTS.exists():
        print(f"[setup_env] 未找到 {REQUIREMENTS}", flush=True)
        return EXIT_FAILED

    print(f"[setup_env] skill 目录:{SKILL_ROOT}", flush=True)
    if venv_python(VENV_DIR).exists():
        print(f"[setup_env] 已存在虚拟环境:{VENV_DIR}(跳过创建)", flush=True)
    else:
        print(f"[setup_env] 创建虚拟环境:{VENV_DIR}", flush=True)
        if run([sys.executable, "-m", "venv", str(VENV_DIR)]) != 0:
            print("[setup_env] venv 创建失败。", flush=True)
            return EXIT_FAILED

    print("[setup_env] 安装依赖(首次约 1~3 GB,含 torch;pip 缓存可加速,请耐心等待)……", flush=True)
    if run([str(venv_python(VENV_DIR)), "-m", "pip", "install",
            "--disable-pip-version-check", "-r", str(REQUIREMENTS)]) != 0:
        print("[setup_env] 依赖安装失败:请检查网络后重试;"
              "必要时先设置镜像(如 `set HF_ENDPOINT=https://hf-mirror.com`)。", flush=True)
        return EXIT_FAILED

    # 用 venv 解释器做最终验证
    verify = run([str(venv_python(VENV_DIR)), "-c", "import docling; print('docling', docling.__version__)"])
    if verify != 0:
        print("[setup_env] 验证失败:venv 中仍无法导入 docling。", flush=True)
        return EXIT_FAILED

    print(f"[setup_env] 完成!之后请始终使用该解释器运行 doc_navigator.py:", flush=True)
    print(f"    {venv_python(VENV_DIR)}", flush=True)
    print("提示:首次解析 PDF 时 docling 会联网下载布局模型;"
          "国内网络可先设置 HF_ENDPOINT=https://hf-mirror.com 与 HF_HUB_DISABLE_XET=1。", flush=True)
    return EXIT_OK


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check_current_interpreter()
    return setup()


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
