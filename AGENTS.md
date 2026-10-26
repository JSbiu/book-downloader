# 项目规则 — book-downloader

## 本机个人上下文

下面一行把本机个人上下文纳入自动加载（相对路径基准 = 本文件所在目录，即仓库根）：

@.local/memory.md

`.local/` 已在 `.gitignore` 中忽略，只对本机生效；文件缺失时该导入静默跳过，不影响其他协作者。

## 协作与文档分工

- 开始工作时读取本文件及根目录 `.local/memory.md`（已由上方导入自动加载），再按任务范围查阅 README 和相关文件。
- 跨项目规则在用户级 `~/.workbuddy/CODEBUDDY.md`，本机环境事实在 `~/.workbuddy/MEMORY.md`；本文件保存项目导航与技术约束。本机记忆不覆盖当前用户指令或适用的 AGENTS.md。
- README 和项目文档保存已核验的共享知识；需求范围、进度、验收与待办放在对应需求或交付文档。本机记忆保留个人偏好、环境事实和必要入口。
- 过程记录按 `## YYYY-MM-DD · 主题` 小节倒序追加在 `.local/memory.md`；先沉淀稳定知识，再收敛记忆。易变事实标明日期、范围与来源，旧验证不代表本轮验证。
- Git 操作遵循当前用户级约定：提供本次精确文件的 add/commit 命令，由用户执行；不沿用历史记忆中的自动提交或推送授权。保留已有暂存、未暂存及未跟踪改动。

## 推进节奏与防御边界（2026-10-25）

- 主任务默认使用窗口当前模型与推理档位；新窗口的 max/low 由 Codex 全局默认、单窗口选择或 Magpie 配置决定，本文件不冒充模型档位开关。执行命令时 `kind: guardian` 的低档自动审查属于正常控制流，不能据此判断主任务被降档；核对时区分主请求与 `guardian` 请求。
- 在用户已授权的目标与路径内，常规实现、离线验证和文件范围内的修复自主完成；只有业务裁决、不可逆操作、外部审批或确实需要用户人工处理的站点验证才暂停等待。
- 先形成一个可审阅的完整批次，再集中汇报结果；过程中只发必要进展。已满足的授权不复问，失败后先做最小根因检查，不堆叠假设性分支、通用框架或重复测试。
- 对耗时较长的执行线程，先确认其确实在推进或已阻塞；不要因速度偏慢反复催问或产生空转消息。完成在当前线程内的独立工作，再与执行线程对齐结果。

## 项目定位与运行环境
- 开发环境为 Ubuntu-22.04 / WSL2，使用项目 `.venv`；Python 版本与依赖以 pyproject.toml 和 requirements.txt 为准，本机运行时位置见 .local/memory.md。Windows WorkBuddy 使用 UNC 文件路径，项目命令通过 WSL 执行。
- 下载章节缓存与合并 TXT 输出尽量放在版本控制之外的目录。

## 目录与结构
- 组织后项目使用 `book_downloader/`，含站点适配器（site adapters）。
- 共享缓存：`cache/<site>/<catalog-url-hash>/`
- 合并输出：`outputs/`
- `cache/`、`outputs/`、`.local/` 已在 .gitignore（另保留 `.workbuddy/` 忽略行作兜底）。

## 原始行为保留
- 原始 downloader 复制自 `trxs_public_chapters_to_txt.py`；保留其「仅公共 HTML」行为。
- 不添加登录、CAPTCHA、付费墙或反爬绕过逻辑。

## 站点适配要点
- 23txxt：搜索表单 `/ar.php` GET、输入框 name=`keyWord`、页面 UTF-8；整域 WAF 需浏览器模式。
  - 不规则页 ID，可能把一章拆到多个 URL；从目录或章节 URL 出发，沿公共续读链接抓取，无 manifest。
  - 目录混合最新章节 / 特章 / 作者公告；适配器按显示序号排主章节，`开始阅读` 视为第 1 章，缓存前过滤公告。
  - 实际缓存文本以错误站名 `二三书库` 开头，重复 `1.标题 (第1/2页)` 类标题，插入 `（本章未完，请点击下一页继续阅读）`；sanitizer 仅移除这些已确认模板，合并缓存块时重跑。
- 69shuba：`/book/<id>.htm` 为详情页（仅最新章节），完整目录在 `/book/<id>/`；目录页顶部有「最新章节」置顶区，需按章节号排序；整域 WAF+Turnstile，仅 `--browser-connect` 可用。
- trxs.cc：搜索 `/e/search/index.php` POST、GB2312；普通模式可用，高频会临时 403。

## 缓存迁移
- 目录重排导致章节位移时，以章节 URL 为身份迁移；保留旧目录为 `chapters.stale-*`，复制可复用文件到新位置。

## 浏览器访问
- 两种模式：Playwright 启动隔离可见 profile；或 `--browser-connect` 经 CDP 附到用户手动启动的正常 Chrome（需人工验证）。
- 不添加 stealth 或挑战绕过逻辑。

## 内容合规
- 源码、测试、README、示例命令、示例日志不得包含具体书名；用中性占位如 `示例小说`，不把真实书名复制进新夹具。
- 不实现自动绕过 Cloudflare / Turnstile / 验证码；人工验证辅助是唯一路径。

## 版本与提交
- Git 提交与推送遵循上方协作约定，不从旧记录推导当前授权。
- `pyproject.toml` 的 version 随提交同步：feat→minor，fix→patch，破坏性→major；同版本多 commit 只更新一次。
- 中文 conventional commit，按功能拆分。

## 测试
- 测试入口见 README：`python -m unittest discover -s tests -v`。确认解释器与依赖后执行，优先使用离线夹具；文档修改只检查相关内容、链接和差异。


## WSL 与编辑器入口

- 本机项目命令在 Ubuntu-22.04 中执行，正式工作区位于 `/home/jason/projects/book-downloader`。
- VS Code 使用 WSL 项目窗口，环境和入口见 README.md 的“本机开发（WSL）”。
- 上述业务、个人资料和生产执行边界继续适用于编辑器操作；配置开发环境不自动授权业务运行或数据写入。
