# 项目规则 — book-downloader

## 协作与文档分工

- 开始工作时读取本文件及根目录 `.local/memory.md`（如存在），再按任务范围查阅 README 和相关文件。
- 用户级 AGENTS.md 保存跨项目规则；本文件保存项目导航与技术约束；本机记忆不覆盖当前用户指令或适用的 AGENTS.md。
- README 和项目文档保存已核验的共享知识；需求范围、进度、验收与待办放在对应需求或交付文档。本机记忆保留个人偏好、环境事实和必要入口。
- 长任务按需使用 `.local/checkpoints/`，历史过程放 `.local/archive/`；先沉淀稳定知识，再收敛记忆。易变事实标明日期、范围与来源，旧验证不代表本轮验证。
- Git 操作遵循当前用户级约定：提供本次精确文件的 add/commit 命令，由用户执行；不沿用历史记忆中的自动提交或推送授权。保留已有暂存、未暂存及未跟踪改动。

## 项目定位与运行环境
- Windows-first 工作流，使用 PowerShell；Python 版本与依赖以 pyproject.toml 和 requirements.txt 为准，本机运行时位置见 .local/memory.md。
- 下载章节缓存与合并 TXT 输出尽量放在版本控制之外的目录。

## 目录与结构
- 组织后项目使用 `book_downloader/`，含站点适配器（site adapters）。
- 共享缓存：`cache/<site>/<catalog-url-hash>/`
- 合并输出：`outputs/`
- `cache/`、`outputs/`、`.workbuddy/` 已在 .gitignore。

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
