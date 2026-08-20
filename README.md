# book-downloader

从一个小说 URL 或已纳入站点的站内搜索结果自动发现目录、抓取章节并合并为 UTF-8 TXT。输入可以是目录页，也可以是任意章节页；程序会尝试从章节页找到目录链接，或者根据已知站点的 URL 结构推断目录页。

项目只请求公开 HTML，不处理登录、验证码、付费墙、隐藏下载接口或反爬验证。遇到 Cloudflare 真人验证时会停止并提示，不会模拟或绕过验证。

## 安装

```powershell
python -m pip install -r requirements.txt
```

## 使用

只需要提供一个 URL：

```powershell
python -m book_downloader "https://www.trxs.cc/tongren/11699/147.html"
```

也可以传目录页：

```powershell
python -m book_downloader "https://www.trxs.cc/tongren/11699.html"
```

也可以直接调用已纳入站点的公开站内搜索，再从结果中选择下载：

```powershell
python -m book_downloader --search "示例小说"
```

程序会显示结果编号、站点、链接和摘要，然后等待输入编号。需要脚本模式时，
可以用 `--search-result 2` 直接选择第二条结果。搜索范围由站点适配器注册表生成，
当前纳入 `trxs.cc`、`23txxt.com` 和笔仙阁镜像；其中只有提供稳定公开搜索入口的站点会参与搜索，
选中的章节页或目录页会继续走原有下载流程。站点搜索遇到真人验证时，可以加上
`--browser`，在可见浏览器中完成正常操作；程序不会绕过验证。

如果关键词包含空格或标点，程序会使用最长的连续关键词请求，并对返回书名做去空格、
去标点匹配，以减少输入格式差异造成的漏搜；每个站点每次搜索只发送一次请求，避免触发站点频控。

输出默认写入 `outputs/<书名>.txt`，缓存默认写入 `cache/<站点>/<目录地址哈希>/`。重新运行同一本书时，已完成章节会自动复用；如果站点调整了目录顺序，程序会按章节 URL 对齐可复用缓存，并保留一份 `chapters.stale-*` 旧目录供恢复。输出 TXT 会根据当前目录和净化规则重新合并，不要求输出文件名固定。

下载过程中按 `Ctrl+C` 会停止后续请求，整理已经完成的缓存并生成部分 TXT；下一次运行相同命令即可继续。

可选参数：

```powershell
python -m book_downloader "https://www.trxs.cc/tongren/11699/147.html" --output .\outputs\11699.txt --delay 1 --timeout 20 --retries 3 --max-pages 50
```

- `--refresh`：忽略章节缓存，重新抓取
- `--max-pages`：单个章节最多跟随多少个“下一页”续页
- `--max-chapters`：只处理目录前 N 个章节，适合先抽样检查；不传则处理全部
- `--max-consecutive-failures`：连续失败多少个待下载章节后停止，默认 5；用于避免异常页面触发长时间连续请求
- `--output`：指定合并后的 TXT 路径
- `--search-results`：搜索模式最多展示的结果数，默认 10
- `--search-result`：搜索模式直接选择的结果编号，从 1 开始

## 23txxt 的浏览器模式

如果站点要求 Cloudflare 真人验证，可以使用可见浏览器模式。脚本不会
破解验证；它会打开独立的 Chrome/Edge 配置目录，你手动完成验证后，脚本
会自动检测页面恢复，后续章节继续使用这个浏览器会话读取公开页面。

先安装可选依赖：

```powershell
python -m pip install "playwright>=1.40"
```

运行（也适用于站内搜索模式）：

```powershell
python -m book_downloader --browser "http://www.23txxt.com/bqg/111084/44601734_2.html" --output .\outputs\111084.txt
```

浏览器会话默认保存在 `cache/browser-profile/`，后续运行可复用。若没有
自动找到浏览器，可用 `--browser-executable` 指定 `chrome.exe` 或
`msedge.exe` 的完整路径。

如果 `--browser` 打开的浏览器一直停在安全验证页，说明站点可能识别出了
自动化启动方式。此时可以让你自己启动普通 Chrome，再让脚本连接它：

```powershell
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
& $chrome --remote-debugging-port=9222 --user-data-dir="D:\workspace\Projects\book-downloader\cache\normal-chrome"
```

在这个 Chrome 窗口中打开链接并手动完成验证，然后保持窗口不关闭，另开
终端运行：

```powershell
python -m book_downloader --browser-connect http://127.0.0.1:9222 "http://www.23txxt.com/bqg/111084/44601734_2.html" --output .\outputs\111084.txt
```

## 站点适配

站点差异放在 `book_downloader/sites/`：

- `trxs_cc.py`：目录识别、正文选择、章节标题净化和简介清理
- `txxt.py`：23txxt 的目录排序、作者公告过滤、正文选择、分页标题/续页提示净化，以及 `_2.html` 续页 URL 规范化
- `bixiange.py`：笔仙阁镜像的 GBK 搜索、目录识别、正文选择、分段合并和首节简介截取
- 站内搜索：由各适配器分别提供 `build_search_request` 和 `parse_search_results`；请求可以按站点声明 GET/POST、表单参数和响应编码，没有稳定公开搜索入口的站点会自动跳过
- `registry.py`：根据输入 URL 自动选择适配器

章节净化是站点级逻辑。以 `trxs.cc` 为例，输出会删除重复的“书名 + 第 N 章”页面标题，保留一份干净的章节标题，并从第 1 章正文前移除作者、简介、推荐语等书籍介绍。

对 23txxt，程序只把同一章节 URL 主干下的 `_2.html`、`_3.html` 视为续页，不会把下一章拼进当前章；合并时会删除站点名“二三书库”、`章节名 (第1/2页)` 等分页标题和“本章未完，请点击下一页继续阅读”提示。已有缓存也会重新应用当前净化规则。

对笔仙阁镜像，目录中的 `index/N.html` 是正文分段入口，不一定对应真实章节。合并时会根据正文中的真实章节标题重新分段，不会把 `第 N 节` 分段标签写入正文；分段缓存仍用于断点续传，已确认的站点污染行也会在重新合并时清除。

## 测试

```powershell
python -m unittest discover -s tests -v
```

## 项目结构

```text
book_downloader/
├─ cli.py                 # URL/站内搜索命令行
├─ search.py              # 多站点站内搜索协调与结果选择
├─ discovery.py           # 从目录页或章节页发现书籍目录
├─ runner.py              # 章节抓取、续页跟随、净化、合并
├─ cache.py               # 按目录 URL 哈希保存缓存
├─ http.py                # GET/POST 请求、编码和验证页检测
├─ browser.py             # 可见浏览器与人工验证/连接模式
└─ sites/
   ├─ base.py             # 站点适配器基类
   ├─ common.py           # 通用目录/正文解析
   ├─ trxs_cc.py          # trxs.cc 适配器
   ├─ txxt.py             # 23txxt 适配器
   ├─ bixiange.py         # 笔仙阁镜像适配器
   └─ registry.py         # URL 到适配器的选择
```
