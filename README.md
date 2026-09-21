# docling-skill — AI 文档结构导航(nanobot Skill)

《智能体开发实战》课程实验 **第 10 题:AI 文档结构导航** 的完整实现。

基于开源项目 **Docling** 解析文本型 PDF 的标题、章节和列表结构,生成层级化章节导航与带页码的目录,
并根据自然语言指令定位目标章节或页面。"跳转至对应页面/章节"通过生成**可点击的章节、页码链接**
实现——依靠目录结果即可定位至目标章节,不调用任何 Docling 以外的外部工具。
整体遵循课程推荐的轻量路线:**用户自然语言 → 智能体(nanobot) → Skill(SKILL.md) → Python Script/CLI → 开源项目(Docling) → 结果**。

## 功能特性

| 意图类别 | 说明 | 对应命令 |
|---|---|---|
| ① 生成目录/层级化章节导航 | 输出 Markdown 目录(默认"章节 \| 页码"表格,子章节带 `└` 层级前缀;`--list` 可改回嵌套列表),`--links` 后章节标题为可点击链接;或结构化 JSON | `toc` / `parse` |
| ② 定位章节/内容 | 按章节标题匹配;未命中自动回退全文检索,返回章节路径、页码范围与内容摘录 | `locate --query` |
| ③ 页面查询 | 返回指定页的内容概要及其所属章节 | `locate --page` |
| ④ 跳转定位 | 生成可点击链接,用户点击即定位到对应页面/章节(不打开任何程序) | `link` |

**链接可点击的关键设计**:浏览器安全策略禁止 http 页面点击 `file://` 链接,终端里裸 URL
文本也不可点击。因此跳转链接默认经由 skill **内嵌的本地 HTTP 服务**提供
(`http://127.0.0.1:8787/文件.pdf#page=N`):纯 Python 标准库实现,只绑定 127.0.0.1、
只服务目标 PDF 这一个文件,由脚本自身以分离进程启动,空闲 15 分钟自动退出;全程不
打开任何阅读器/浏览器。webui 中链接渲染为 `<a>`,终端(Rich)中渲染为 Ctrl+点击
超链接,均可直接打开并定位到具体页(`#page=N` 由浏览器内置 PDF 查看器支持)。

服务不可用时(端口 8787~8797 全被占用、启动进程失败等)`link`/`toc --links`
**不会整体失败**:自动退回 `file:///` 链接,并在 `data.url_scheme=file` 与 `message`
中如实说明,链接照常可用(浏览器可能拦截,终端 Ctrl+点击或本地阅读器可打开)。

**目录为何默认是表格而非嵌套列表**:实测发现部分聊天前端(nanobot webui)渲染
助手消息时会把相邻行合并成一行,嵌套列表的子项会被并进父项(表现为"第四章大标题
和 4.1~4.4 挤在同一行")。表格每行以 `|` 分隔、层级用 `└` 前缀、页码独立成列,
即使行被合并也仍是完整可读的一行;`toc --list` 可切回嵌套列表供终端使用。

**可点击目录为何只链接章节标题**:章节标题与页码指向同一个 URL,两处都写成链接会让
长目录里的 URL 重复一遍、token 近乎翻倍。实测一份 58 条的目录:两列都带链接约
9981 tokens,只标题带链接约 5877 tokens,而聊天前端对单条消息有输出上限(nanobot
默认 8192 tokens)——超限后消息会被从中间截断,续写部分失去表头,表格后半段就渲染
不成表格(表现为"表格没渲染全"、"多出一条消息")。页码列保持纯文本即可正常阅读,
跳转点标题即可。

- 仅支持**文本型 PDF**(Word 导出/原生生成);扫描件需 OCR,不支持。
- 页码一律为 **PDF 物理页码**(从 1 起),可能与文档印刷页码不同。
- CLI 可完全独立运行(不依赖 nanobot),便于单独测试定位问题。

## 用户场景

目标用户是"手边有一份(或多份)长 PDF、需要快速搞清结构与位置"的人,典型场景:

1. **看结构**:课程任务书/技术报告/规范文档拿到手,先要一份带页码的层级目录——
   "帮我列出这份 PDF 的目录结构",得到 Markdown 目录或 `toc --links` 的可点击导航。
2. **找位置**:开卷考试式查资料——"评分标准在哪个部分?第几页?"、"哪一章讲了低 Token
   优化?",`locate --query` 直接给出章节路径、页码范围与摘录;标题没命中会自动回退正文检索。
3. **看某页**:拿到别人的批注说"第 3 页有问题",先问"第 3 页讲了什么?",`locate --page`
   返回该页内容概要及所属章节。
4. **跳过去**:确认位置后要真的翻到那一页——"跳到评分标准那一章",`link` 生成可点击链接,
   点击即在浏览器中打开 PDF 并定位到该页;整份导航用 `toc --links` 一次给全。

## 安全措施

| 措施 | 说明 |
|---|---|
| 只读不改 | 全程不写、不删、不移动用户的 PDF;仅在 skill 目录内写解析缓存与日志 |
| 网络最小化 | 内嵌 HTTP 服务只绑定 `127.0.0.1`(不对外网/局域网开放),只服务当前这一个 PDF 文件,其余路径一律 404,空闲 15 分钟自动退出 |
| 不打开任何程序 | 不调用浏览器/阅读器/PowerShell 等外部程序,不点击桌面;跳转由用户点击链接完成 |
| 无高风险操作 | 不涉及文件写入/移动/删除、网页提交、桌面点击,故无需用户确认环节;唯一副作用是启动本机回环监听与写缓存/日志 |
| 日志脱敏 | `logs/doc_navigator.log` 只记时间、命令、文档名、耗时与结果摘要,不含文档正文,更不含密钥 |
| 无外部模型 | 解析全在本地由 Docling 完成,不向外发送文档内容 |

## 项目结构

```
docling-skill/
├── SKILL.md                  # nanobot Skill 主文件(部署到 nanobot 后由智能体读取)
├── scripts/
│   └── doc_navigator.py     # 核心 CLI(parse / toc / locate / link)
├── scripts/setup_env.py     # 首次初始化引导:创建 skill 自带 venv 并安装依赖(可移植)
├── tests/
│   ├── test_doc_navigator.py # 测试用例(正常/边界/异常)
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

# 生成 Markdown 目录(默认表格形态;--json 得到结构化目录)
.venv\Scripts\python.exe scripts\doc_navigator.py toc 文档.pdf

# 终端场景可改回嵌套列表形态
.venv\Scripts\python.exe scripts\doc_navigator.py toc 文档.pdf --list

# 定位:章节/关键词 → 章节路径 + 页码范围 + 摘录
.venv\Scripts\python.exe scripts\doc_navigator.py locate 文档.pdf --query "评分标准"

# 定位:页码 → 该页内容概要与所属章节
.venv\Scripts\python.exe scripts\doc_navigator.py locate 文档.pdf --page 3

# 生成可点击目录(章节标题为 #page=N 链接,点击即定位)
.venv\Scripts\python.exe scripts\doc_navigator.py toc 文档.pdf --links

# 跳转定位:生成指向对应页面/章节的可点击链接(只生成链接,不打开任何程序)
.venv\Scripts\python.exe scripts\doc_navigator.py link 文档.pdf --page 7
.venv\Scripts\python.exe scripts\doc_navigator.py link 文档.pdf --section "评分标准"

# 兜底:退回 file:/// 链接(默认 http 链接;仅本机访问场景可用 http)
.venv\Scripts\python.exe scripts\doc_navigator.py link 文档.pdf --page 7 --file-url

# 可点击目录同样可退回 file:/// 链接(--file-url 隐含 --links)
.venv\Scripts\python.exe scripts\doc_navigator.py toc 文档.pdf --file-url
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
   - 常用问法:"评分标准在哪个部分/第几页?"、"第 3 页讲了什么?"、"跳到第 5 页"
     (跳转通过生成可点击的 `#page=N` 链接完成,智能体会把链接发给用户点击)。

> 说明:目标机器只需有任意 Python ≥3.10 用于跑一次引导脚本;
> 首次解析 PDF 时 Docling 需联网下载布局模型,国内网络按"安装"一节设置镜像环境变量。

## 测试方法

```bash
cd tests
..\.venv\Scripts\python.exe -m pytest test_doc_navigator.py -v
```

23 个用例覆盖:结构解析、目录生成(表格/列表两种形态、JSON、可点击链接)、章节定位、全文检索回退、
页码查询、跳转链接生成(按页/按章节/内嵌 HTTP 服务可取回 PDF 且其余路径 404)、
内嵌服务复用与端口占用时的 file:/// 降级,以及未命中/页码越界/文件不存在/非 PDF 等
异常分支、日志计数与环境自检。测试通过子进程调用 CLI,与 nanobot 的实际调用方式一致。

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

## 许可证

本项目以 [MIT License](LICENSE) 开源。项目自身未修改 Docling 源码,仅按其 MIT 许可证调用;
测试样例生成依赖的 PyMuPDF 为 AGPL-3.0(或商业授权),仅用于开发/测试环境,
不随项目分发,详见上方「开源依赖及许可证」。

## 已知问题

- 标题层级按编号约定推断(如 `4.1` 为二级);**无编号**的标题统一按一级处理,层级可能弱于原文视觉层级。
- 文档标题取首个识别到的标题(通常首页),极少数文档可能把首个正文标题误认为文档标题。
- `#page=N` 链接点击后能否定位到具体页取决于打开链接的程序(Edge/Chrome 等浏览器支持)。
- 扫描件/图片型 PDF 无 OCR 支持,会解析出极少的结构并提示按文本理解。
- **http 链接有时效**:内嵌服务空闲 15 分钟后自动退出,此后点旧链接会提示"无法访问此网站";
  重新跑一次 `link`/`toc --links` 通常会把服务起在同一个端口,旧链接随之恢复。
- **端口占用**:同时服务超过 11 个 PDF(端口 8787~8797 用满)时,新链接自动降级为
  `file:///` 形式(`data.url_scheme=file`),不会失败但可点击性依赖打开方式。
