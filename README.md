# docling-skill — AI 文档结构导航(nanobot Skill)

《智能体开发实战》课程实验 **第 10 题:AI 文档结构导航** 的完整实现。

基于开源项目 **Docling** 解析文本型 PDF 的标题、章节和列表结构,生成带页码的目录,
并根据自然语言指令定位目标章节或页面;支持生成跳转链接/调用阅读器打开对应页面(可选功能)。
整体遵循课程推荐的轻量路线:**用户自然语言 → 智能体(nanobot) → Skill(SKILL.md) → Python Script/CLI → 开源项目(Docling) → 结果**。

## 功能特性

| 意图类别 | 说明 | 对应命令 |
|---|---|---|
| ① 生成目录/查看结构 | 输出 Markdown 目录(层级缩进 + 页码),或结构化 JSON | `toc` / `parse` |
| ② 定位章节/内容 | 按章节标题匹配;未命中自动回退全文检索,返回章节路径、页码范围与内容摘录 | `locate --query` |
| ③ 页面查询 | 返回指定页的内容概要及其所属章节 | `locate --page` |
| ④ 跳转打开(可选功能) | 生成 `file:///...#page=N` 链接;确认后可调用系统默认阅读器打开 | `open` |

- 仅支持**文本型 PDF**(Word 导出/原生生成);扫描件需 OCR,不支持。
- 页码一律为 **PDF 物理页码**(从 1 起),可能与文档印刷页码不同。
- CLI 可完全独立运行(不依赖 nanobot),便于单独测试定位问题。

## 项目结构

```
docling-skill/
├── SKILL.md                  # nanobot Skill 主文件(部署到 nanobot 后由智能体读取)
├── scripts/
│   └── doc_navigator.py     # 核心 CLI(parse / toc / locate / open)
├── scripts/setup_env.py     # 首次初始化引导:创建 skill 自带 venv 并安装依赖(可移植)
├── tests/
│   ├── test_doc_navigator.py # 13 个测试用例(正常/边界/异常)
│   └── make_sample_pdf.py    # 用 PyMuPDF 生成多级标题样例 PDF
├── test_data/                # 测试文档(课程任务书 + 生成的样例)
├── logs/doc_navigator.log    # 运行日志(自动生成,不含敏感信息)
├── .cache/                   # 解析结果缓存(自动生成)
├── requirements.txt
└── README.md
```

> 部署时只需复制 `SKILL.md`、`scripts/`、`requirements.txt` 三项;skill 不依赖任何固定
> Python 环境,在目标机器上首次使用时由 `scripts/setup_env.py` 自动创建自带虚拟环境。

## 安装

需要 Python 3.10+(实测 3.11)。skill 自带引导脚本,在项目根目录运行一次即可:

```bash
python scripts/setup_env.py
```

它会在当前 skill 目录下创建 `.venv` 并安装 requirements.txt(docling、pymupdf、pytest),
完成后所有命令统一使用 `.venv\Scripts\python.exe`。也可以手动创建:

```bash
py -3.11 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

> 国内网络提示:首次解析 PDF 时 Docling 需从 HuggingFace 下载布局模型。
> 若直连超时,设置镜像后再运行:
> ```bash
> set HF_ENDPOINT=https://hf-mirror.com
> set HF_HUB_DISABLE_XET=1
> ```
> 模型下载一次后缓存在本地,之后可完全离线。

## 运行方法(CLI 独立使用)

```bash
# 解析结构(标题/章节树/列表统计)
.venv\Scripts\python.exe scripts\doc_navigator.py parse 文档.pdf

# 生成 Markdown 目录(--json 得到结构化目录)
.venv\Scripts\python.exe scripts\doc_navigator.py toc 文档.pdf

# 定位:章节/关键词 → 章节路径 + 页码范围 + 摘录
.venv\Scripts\python.exe scripts\doc_navigator.py locate 文档.pdf --query "评分标准"

# 定位:页码 → 该页内容概要与所属章节
.venv\Scripts\python.exe scripts\doc_navigator.py locate 文档.pdf --page 3

# 跳转:生成打开对应页面的链接
.venv\Scripts\python.exe scripts\doc_navigator.py open 文档.pdf --page 7
# 实际调用系统阅读器打开(建议先征得用户确认)
.venv\Scripts\python.exe scripts\doc_navigator.py open 文档.pdf --page 7 --launch
```

统一输出 JSON(`status`/`message`/`data`),退出码:0 成功、2 未找到、3 文件错误、4 解析失败。

## 部署到 nanobot(可移植)

skill 不绑定任何固定 Python 环境或路径,可以原样部署到任意设备的 nanobot:

1. 把 `SKILL.md`、`scripts/`、`requirements.txt` 复制到目标机器的
   `~/.nanobot/workspace/skills/doc-navigator/`(目录名须为 `doc-navigator`,与 skill 名一致):
   ```bash
   mkdir -p ~/.nanobot/workspace/skills/doc-navigator
   cp SKILL.md ~/.nanobot/workspace/skills/doc-navigator/SKILL.md
   cp -r scripts requirements.txt ~/.nanobot/workspace/skills/doc-navigator/
   ```
2. 首次使用时初始化环境(智能体会按 SKILL.md 的指引自动引导,也可手动执行):
   ```bash
   python ~/.nanobot/workspace/skills/doc-navigator/scripts/setup_env.py
   ```
   该脚本在 skill 目录内创建 `.venv` 并安装依赖(约 1~3 GB,含 torch;
   pip 本地缓存可加速)。之后 skill 完全使用自带解释器运行,与系统环境解耦。
3. (重)启动 nanobot(`nanobot webui` 或 `nanobot gateway`),在对话中:
   - `$doc-navigator` 显式调用,或直接说"帮我列出 xxx.pdf 的目录";
   - 常用问法:"评分标准在哪个部分/第几页?"、"第 3 页讲了什么?"、"打开第 5 页"(打开前智能体会先征求确认)。

> 说明:目标机器只需有任意 Python ≥3.10 用于跑一次引导脚本;
> 首次解析 PDF 时 Docling 需联网下载布局模型,国内网络按"安装"一节设置镜像环境变量。

## 测试方法

```bash
cd tests
..\.venv\Scripts\python.exe -m pytest test_doc_navigator.py -v
```

13 个用例覆盖:结构解析、目录生成(Markdown/JSON)、章节定位、全文检索回退、页码查询、
跳转链接生成,以及未命中/页码越界/文件不存在/非 PDF 等异常分支和环境自检。
测试通过子进程调用 CLI,与 nanobot 的实际调用方式一致。

## 开源依赖及许可证

| 依赖 | 版本 | 许可证 | 用途 |
|---|---|---|---|
| [Docling](https://github.com/docling-project/docling) | 2.126.0 | MIT | 核心解析引擎(布局模型识别标题/章节/列表,关闭 OCR 与表格模型) |
| [PyMuPDF](https://pymupdf.readthedocs.io/) | 1.28.2 | AGPL-3.0(或商业授权) | 仅用于生成测试样例 PDF(`tests/make_sample_pdf.py`) |
| [pytest](https://pytest.org) | 9.1.1 | MIT | 测试框架 |

Docling 运行还会间接引入 torch、transformers 等依赖,详见其项目声明;本项目未修改其源码,仅按 MIT 许可证引用。

## 低 Token / 性能优化

1. **解析结果缓存**:按文件 SHA-256 + Docling 版本缓存解析结构(`.cache/`),同一文档重复定位从约 26s 降到 <0.1s,且不重复占用算力;
2. **摘录截断**:定位结果默认只返回 300 字摘录、页码查询默认 800 字,均可调,避免把整页/整文档内容塞进模型上下文;
3. **结构化紧凑输出**:stdout 单行 JSON,过程信息走 stderr。

## 已知问题

- 标题层级按编号约定推断(如 `4.1` 为二级);**无编号**的标题统一按一级处理,层级可能弱于原文视觉层级。
- 文档标题取首个识别到的标题(通常首页),极少数文档可能把首个正文标题误认为文档标题。
- `#page=N` 跳转是否定位到具体页取决于阅读器(Edge/Chrome 等浏览器支持)。
- 扫描件/图片型 PDF 无 OCR 支持,会解析出极少的结构并提示按文本理解。
