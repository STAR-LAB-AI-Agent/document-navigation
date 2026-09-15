---
name: doc-navigator
description: 解析文本型 PDF(非扫描件)的文档结构(标题/章节/列表),生成带页码的目录,并按用户指令定位目标章节或页面,还可生成跳转链接打开对应页面。只要用户提到某个 PDF 并询问其目录/结构/章节,或问"某内容在第几页/哪个章节"、"某一页讲了什么",或要求跳转到某页/某章——即使没有明确说出"目录""结构"等字眼——都应使用本 skill。
metadata: {"nanobot":{"emoji":"🧭"}}
---

# Doc Navigator(文档结构导航)

对**文本型 PDF**(Word 导出、原生生成等,非扫描件)做结构解析与导航。所有能力通过一个本地 CLI 提供,输出统一为 UTF-8 JSON。

## 何时使用

用户提到某个 PDF(或明确给了 PDF 路径),并且想:
1. **看目录/结构** —— "帮我列出这份 PDF 的目录"、"这个文档有哪些章节?"
2. **定位章节** —— "评分标准在哪个部分/第几页?"、"实验内容那一章讲了什么?"
3. **查询页面** —— "第 3 页讲了什么?"、"哪一页提到了 nanobot?"
4. **跳转打开** —— "帮我打开第 5 页"、"跳到评分标准那一章"(可选功能)

仅支持文本型 PDF;扫描件/图片型 PDF(需 OCR)不支持,应明确告知用户。

## 调用方式

所有路径都相对于**本 skill 目录**(记为 `<skill_dir>`,即本 SKILL.md 所在目录,
nanobot 默认部署位置为 `~/.nanobot/workspace/skills/doc-navigator/`):

- 解释器:`<skill_dir>\.venv\Scripts\python.exe`(Windows)或 `<skill_dir>/.venv/bin/python`(macOS/Linux)
- 脚本:`<skill_dir>\scripts\doc_navigator.py`

命令模板(路径含空格时务必加引号):

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" <子命令> "<PDF路径>" [参数]
```

下文示例均用 Windows 写法;macOS/Linux 把 `\` 换成 `/`、解释器换成
`<skill_dir>/.venv/bin/python` 即可,参数不变。

### 首次使用:环境初始化(可移植部署)

本 skill 不依赖任何固定 Python 环境。若 `<skill_dir>\.venv` 不存在,或运行时报告
「未找到 docling 库」,请用系统里**任意一个 Python ≥3.10** 执行一次初始化:

```bash
python "<skill_dir>\scripts\setup_env.py"
```

它会在 skill 目录内创建自带虚拟环境并安装依赖(约 1~3 GB,数分钟)。完成后改用
`<skill_dir>\.venv\...` 解释器。判断是否已初始化可运行:

```bash
python "<skill_dir>\scripts\setup_env.py" --check
```

国内网络首次解析 PDF 下载模型失败时,先设置 `HF_ENDPOINT=https://hf-mirror.com`
与 `HF_HUB_DISABLE_XET=1` 再重试(模型下载一次后即离线可用)。

## 子命令与意图映射

| 用户意图 | 子命令 | 关键参数 |
|---|---|---|
| 列目录/看结构 | `toc <pdf>` | 无;`--json` 可要结构化目录 |
| 解析完整结构 | `parse <pdf>` | `--list-items` 输出列表项明细 |
| 定位章节/内容 | `locate <pdf> --query "关键词"` | `--excerpt-chars 300` 控制摘录长度 |
| 查询某页内容 | `locate <pdf> --page 3` | `--max-chars 800` 控制文本长度 |
| 跳转/打开页面 | `open <pdf> --page 5` 或 `--section "标题"` | `--launch` 实际打开阅读器 |

## 输出解读

stdout 为单行 JSON(进度信息走 stderr,忽略即可):

- `status`:`ok` 成功 / `no_match` 未找到 / `error` 出错;配套 `message` 是给用户看的中文说明。
- `toc`/`parse`:`data.toc_markdown` 是 Markdown 目录;`data.sections[]` 含 `title/level/path/page_start/page_end/list_count`。
- `locate --query`:`data.match_type` 为 `section`(命中章节标题)或 `content`(正文检索);`matches[]` 含章节路径、页码范围与 `excerpt` 摘录。
- `locate --page`:`data.text` 是该页文本(已截断),`data.sections_on_page` 是该页所属章节。
- `open`:`data.url` 是 `file:///...#page=N` 链接;`data.local_path` 是本地绝对路径(链接打不开时的兜底);`data.launched` 表示是否已实际打开。
- 退出码:0 成功,2 未找到,3 文件错误(不存在/非 PDF),4 解析失败。

## 使用要点

- **低 Token**:向用户转述时优先用 `message` 和 `excerpt`,不要整段复述 `data.text` 或整棵章节树。
- **未命中(no_match)**:先如实告知,再建议用户换更具体的关键词,或主动改用 `toc` 让用户从目录中挑。
- **用户问"第几页"**:直接引用 `page_start`(及范围);PDF 页码为物理页码,若文档印刷页码不同应提醒。
- **跳转安全确认**:`open ... --launch` 会实际打开阅读器,执行前必须先向用户确认。Windows 上会
  优先直接调用 Edge/Chrome 打开 `#page=N` 链接(可靠跳页);找不到浏览器则退回系统默认阅读器
  打开文件本身(无法跳页,`page_jump=false` 会在 message 中注明)。macOS/Linux 走浏览器打开。
- **链接打不开时**:个别 Windows 环境的 `file:` 协议默认处理程序会把 URL 截断,报
  「Windows 找不到文件」。此时转述 `data.local_path` 让用户手动打开,并告知页码;
  或建议用户改用 `--launch` 由脚本直接调浏览器打开(已绕开该系统缺陷)。
- **文件路径**:用户说"这份/那个 PDF"时,先确认具体文件路径再调用;找不到文件(退出码 3)时如实转述。
- **解释器错误**:若 `error` 消息中出现 `未找到 docling 库`,说明 skill 自带环境未初始化或用错了
  解释器——先按上方「首次使用」运行 `scripts/setup_env.py`,再改用 `<skill_dir>\.venv\...` 解释器重试;
  不要自行尝试 pip install 到系统环境。

## 示例

### 示例 1:生成目录

用户:帮我看看 `D:\docs\课程实验任务书.pdf` 的目录结构。

动作:

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" toc "D:\docs\课程实验任务书.pdf"
```

得到 `status=ok`,`data.toc_markdown` 类似:

```markdown
# 目录 | 《智能体开发实战》课程实验任务书
> 共 7 页 · 12 个章节条目
- 一、实验目的(第2页)
- 二、实验内容(第2-4页)
  ...
```

把该 Markdown 目录直接呈现给用户,并提示页码为 PDF 物理页码。

### 示例 2:定位章节 + 跳转确认

用户:"评分标准在哪个部分?顺便打开那一页。"

第一步:

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" locate "D:\docs\课程实验任务书.pdf" --query "评分标准"
```

返回 `match_type=section`,`matches[0]` 为 `{"title":"六、评分标准","path":"六、评分标准","page_start":7,"page_end":7,"excerpt":"评分项目 分值 评价要点 …"}`。先回答用户:评分标准在「六、评分标准」第 7 页,并给出一两句摘录。

第二步,打开前先确认:"要我调用系统阅读器打开第 7 页吗?"用户同意后再执行:

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" open "D:\docs\课程实验任务书.pdf" --page 7 --launch
```

Windows 上脚本会直接用 Edge/Chrome 打开该页(可靠跳页);`status=ok` 且 `launched=true` 即已打开。
若用户不同意,则把 `data.url` 链接发给用户让其自行点击,并附上 `data.local_path` 与页码作为兜底说明。

### 示例 3:页码查询

用户:"第 3 页讲了什么?"

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" locate "D:\docs\课程实验任务书.pdf" --page 3
```

返回 `data.sections_on_page`(所在章节)与 `data.text`(页面前 800 字,`truncated` 表示有截断)。用 2~3 句话概括该页内容回答,不要全文复述。
