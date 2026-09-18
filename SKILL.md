---
name: doc-navigator
description: 解析文本型 PDF(非扫描件)的标题/章节/列表结构,生成层级化目录与章节导航,并按用户指令定位目标章节或页面、生成可点击的跳转链接。只要用户提到某个 PDF 并询问其目录/结构/章节,或问"某内容在第几页/哪个章节"、"某一页讲了什么",或要求跳到某页/某章——即使没有明确说出"目录""结构"等字眼——都应使用本 skill。
metadata: {"nanobot":{"emoji":"🧭"}}
---

# Doc Navigator(文档结构导航)

对**文本型 PDF**(Word 导出、原生生成等,非扫描件)做结构解析与导航。所有能力由一个本地 CLI 提供,输出统一为 UTF-8 JSON。

## 何时使用

用户提到某个 PDF(或明确给了 PDF 路径),并且想:
1. **看目录/结构** —— "帮我列出这份 PDF 的目录"、"这个文档有哪些章节?"
2. **定位章节** —— "评分标准在哪个部分/第几页?"、"实验内容那一章讲了什么?"
3. **查询页面** —— "第 3 页讲了什么?"、"哪一页提到了 nanobot?"
4. **跳转/定位** —— "跳到评分标准那一章"、"定位到第 5 页":生成指向该章节/页码的
   **可点击链接**交给用户点击,或给出**层级化可点击目录**让用户自行点击。

**边界**:"跳转"仅指生成链接,由用户点击完成定位。本 skill **不会、也不应**调用 Docling
以外的任何外部工具(浏览器命令行、os.startfile、webbrowser、PowerShell 等)去实际打开或
切换页面;用户若要求"帮我打开阅读器",应说明只能生成链接,请其自行点击。

**链接为何默认用 http**:浏览器会拦截页面里的 `file://` 链接,所以链接默认经由 skill
**内嵌的本地 HTTP 服务**提供(`http://127.0.0.1:8787/文件名#page=N`;Python 标准库实现,
只绑 127.0.0.1、只服务目标 PDF、空闲 15 分钟自动退出,不打开任何阅读器)。服务起不来时
(端口 8787~8797 全被占用等)脚本**不会报错失败**,而是自动退回 `file:///` 链接并把
`data.url_scheme` 标为 `file`;此时链接照常发给用户,按下方「`url_scheme=file`」处理。

**只读**:全程不写、不删、不移动用户的 PDF,只在 skill 目录内写解析缓存与日志;除启动这个
本机回环监听外无任何副作用,因此不涉及需要用户确认的高风险操作。

## 调用方式

路径都相对于**本 skill 目录**(记为 `<skill_dir>`,即本 SKILL.md 所在目录;nanobot 默认
部署位置 `~/.nanobot/workspace/skills/doc-navigator/`):

- 解释器:`<skill_dir>\.venv\Scripts\python.exe`(Windows)或 `<skill_dir>/.venv/bin/python`(macOS/Linux)
- 脚本:`<skill_dir>\scripts\doc_navigator.py`

```bash
"<skill_dir>\.venv\Scripts\python.exe" "<skill_dir>\scripts\doc_navigator.py" <子命令> "<PDF路径>" [参数]
```

路径含空格时务必加引号。macOS/Linux 把 `\` 换成 `/`、解释器换成 `<skill_dir>/.venv/bin/python`,
参数不变。

### 首次使用:初始化环境(可移植部署)

本 skill 不依赖任何固定 Python 环境。若 `<skill_dir>\.venv` 不存在,或运行时报告
「未找到 docling 库」,用系统里**任意 Python ≥3.10** 执行一次:

```bash
python "<skill_dir>\scripts\setup_env.py"          # 创建自带 venv 并安装依赖(约 1~3 GB,数分钟)
python "<skill_dir>\scripts\setup_env.py" --check  # 判断是否已初始化
```

完成后改用自带解释器运行,**不要**自行 pip install 到系统环境。国内网络首次解析 PDF 需下载
模型,失败时先设置 `HF_ENDPOINT=https://hf-mirror.com` 与 `HF_HUB_DISABLE_XET=1` 再重试
(模型下载一次后即离线可用)。

## 子命令与意图映射

| 用户意图 | 子命令 | 关键参数 |
|---|---|---|
| 列目录/看结构/章节导航 | `toc <pdf>` | `--links` 生成可点击目录;`--json` 输出结构化目录;`--file-url` 用 file:/// 链接(隐含 `--links`);`--list` 改回嵌套列表 |
| 解析完整结构 | `parse <pdf>` | `--list-items` 输出列表项明细 |
| 定位章节/内容 | `locate <pdf> --query "关键词"` | `--excerpt-chars 300` 摘录长度;`--max-matches 5` 匹配数 |
| 查询某页内容 | `locate <pdf> --page 3` | `--max-chars 800` 文本长度 |
| 跳转(生成链接) | `link <pdf> --page 5` 或 `--section "标题"` | `--file-url` 退回 file:/// 兜底 |

## 输出解读

stdout 为单行 JSON(进度信息走 stderr,忽略即可);`status` 为 `ok`/`no_match`/`error`,
`message` 是给用户看的中文说明。

- `toc`:默认只返回 `data.toc_markdown`(Markdown 目录:"章节 | 页码"两列表格,子章节在
  章节名前缀 `└`,页码支持范围如"第2-4页";加 `--list` 改回嵌套列表)。`--links` 时只有
  **章节标题**可点击(标题与页码本是同一 URL,只链一处以免长目录 token 翻倍);加 `--json`
  额外返回 `data.title`、`data.page_count` 与 `data.sections[]`(含
  `title/level/path/page_start/page_end`)。要章节数组必须带 `--json`。
- `parse`:`data.sections[]`(同上字段,另含 `list_count`)、`data.lists_total`、
  `data.title`、`data.page_count`;不含目录 Markdown。
- `locate --query`:`data.match_type` 为 `section`(命中章节标题)或 `content`(正文检索);
  `matches[]` 含章节路径、页码范围与 `excerpt` 摘录。
- `locate --page`:`data.text` 是该页文本(已截断),`data.sections_on_page` 是该页所属章节。
- `link`:`data.url` 是可点击跳转链接,`data.target` 是目标的人类可读描述,
  `data.local_path` 是本地绝对路径。
- `data.url_scheme`(`link` 与带链接的 `toc` 都有):`http` 表示经内嵌服务,**优先**;
  `file` 表示已退回 `file:///`(显式 `--file-url`,或内嵌服务起不来时自动降级,
  后者 `data.http_server.error` 给出原因)。
- 退出码:0 成功,2 未找到,3 文件错误(不存在/非 PDF),4 解析失败。

## 使用要点

- **低 Token**:向用户转述时优先用 `message` 和 `excerpt`,不要整段复述 `data.text` 或整棵章节树。
- **`toc_markdown` 必须原样转述**:表格形态是刻意设计的——部分聊天前端(nanobot webui 等)
  会把助手消息里相邻的行合并成一行,嵌套列表的子项会因此挤进父项同一行(看起来像"第四章
  大标题和 4.1~4.4 排成一行");表格每行有 `|` 分隔,即使被合并也仍是完整行。**不要**手工
  改成嵌套列表或加缩进,也不要用代码块包裹(代码块会失去可点击链接)。
- **目录很长时别再叠加内容**:单条消息有输出上限(nanobot 默认 8192 tokens),超限会被从
  中间截断、续写部分失去表头而渲染不出表格。`toc_markdown` 之外只补一两句说明,不要为每个
  章节再写摘要或重复罗列条目。
- **链接必须用 Markdown 语法**:把 `data.url` 包进 `[标题 · 第N页](链接)` 再发给用户
  (如 `[六、评分标准 · 第 7 页](http://127.0.0.1:8787/xx.pdf#page=7)`)。**禁止**输出裸
  URL 文本行或用代码块包住 URL——webui 与终端都只对 Markdown 链接语法渲染可点击超链接。
  不要手拼链接。
- **未命中(no_match)**:先如实告知,再建议用户换更具体的关键词,或主动改用 `toc --links`
  让用户从可点击目录中自己挑。
- **用户问"第几页"**:直接引用 `page_start`(及范围);PDF 页码为物理页码,若文档印刷页码
  不同应提醒。
- **`url_scheme=file` 或用户反馈链接打不开时**:如实说明该链接是 `file:///` 形式、部分浏览器
  会拦截,建议在终端里 Ctrl+点击,或用本地阅读器打开 `data.local_path` 并翻到对应页;也可
  稍后重跑一次 `link` 再取 http 链接。另注意两点——① http 链接只在跑 nanobot 的那台机器
  可点(webui 从另一台机器访问时点不开);② 内嵌服务空闲 15 分钟自动退出,旧链接会失效,
  **重跑一次 `link` 或 `toc --links` 通常会重新占用同一端口、旧链接随之恢复**,不必向用户
  解释端口细节,直接重发链接即可。
- **文件路径**:用户说"这份/那个 PDF"时,先确认具体文件路径;找不到文件(退出码 3)时如实转述。

## 示例

以下示例用 `<py>` 代指 `"<skill_dir>\.venv\Scripts\python.exe"`(macOS/Linux 换成
`<skill_dir>/.venv/bin/python`)。

### 示例 1:生成可点击目录(层级化章节导航)

用户:"帮我看看 `D:\docs\课程实验任务书.pdf` 的目录结构,并做成可点击的导航。"

```bash
<py> "<skill_dir>\scripts\doc_navigator.py" toc "D:\docs\课程实验任务书.pdf" --links
```

得到 `status=ok`,`data.toc_markdown` 形如(页码列为纯文本,只有章节标题可点):

```markdown
# 目录 | 《智能体开发实战》课程实验任务书
> 共 7 页 · 12 个章节条目

| 章节 | 页码 |
| --- | --- |
| [一、实验目的](http://127.0.0.1:8787/%E8%AF%BE%E7%A8%8B.pdf#page=2) | 第2页 |
| [二、实验内容](http://127.0.0.1:8787/%E8%AF%BE%E7%A8%8B.pdf#page=2) | 第2-4页 |
| [四、总体要求](http://127.0.0.1:8787/%E8%AF%BE%E7%A8%8B.pdf#page=5) | 第5页 |
| [　└4.1 基本技术要求](http://127.0.0.1:8787/%E8%AF%BE%E7%A8%8B.pdf#page=5) | 第5页 |
  ...
```

把该 Markdown 目录**原样**发给用户(webui 渲染成两列表格、章节名可点击;终端里是
Ctrl+点击可打开的超链接),并提示页码为 PDF 物理页码。用户想要终端里的嵌套列表形态加
`--list`;只要目录不要链接则去掉 `--links`。

### 示例 2:定位章节 + 生成跳转链接

用户:"评分标准在哪个部分?给我跳到那里。"

```bash
<py> "<skill_dir>\scripts\doc_navigator.py" locate "D:\docs\课程实验任务书.pdf" --query "评分标准"
<py> "<skill_dir>\scripts\doc_navigator.py" link   "D:\docs\课程实验任务书.pdf" --section "评分标准"
```

`locate` 返回 `match_type=section`,`matches[0]` 为
`{"title":"六、评分标准","path":"六、评分标准","page_start":7,"page_end":7,"excerpt":"评分项目 分值 评价要点 …"}`:
先回答"评分标准在「六、评分标准」第 7 页"并给出一两句摘录;再把 `link` 的 `data.url` 用
Markdown 链接发给用户(链接文本用 `data.target`),如
`[「六、评分标准」(第 7 页)](http://127.0.0.1:8787/%E8%AF%BE%E7%A8%8B…pdf#page=7)`,
用户点击即打开该 PDF 并定位到第 7 页。

### 示例 3:页码查询

用户:"第 3 页讲了什么?"

```bash
<py> "<skill_dir>\scripts\doc_navigator.py" locate "D:\docs\课程实验任务书.pdf" --page 3
```

返回 `data.sections_on_page`(所在章节)与 `data.text`(页面前 800 字,`truncated` 表示有
截断)。用 2~3 句话概括该页内容回答,不要全文复述;若用户接着想跳过去,按示例 2 生成第 3 页
的链接即可。
