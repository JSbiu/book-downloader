# 项目规则 — book-downloader

## 项目定位与运行环境
- 项目根：`D:\workspace\Projects\book-downloader`
- Windows-first 工作流；优先使用 PowerShell 与 bundled managed Python runtime。
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
- 完成一批实质工作后主动整理提交并推送（用户已授权，见全局 `AGENTS.md`）。
- `pyproject.toml` 的 version 随提交同步：feat→minor，fix→patch，破坏性→major；同版本多 commit 只更新一次。
- 中文 conventional commit，按功能拆分。

## 测试
- 系统 Python：`D:\dev\python\python.exe -m unittest discover -s tests -v`（managed Python 未装 bs4 / requests / playwright）。
