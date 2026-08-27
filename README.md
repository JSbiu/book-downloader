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
当前纳入 `trxs.cc`、`23txxt.com`、笔仙阁镜像和 `69shuba.com`；其中只有提供稳定公开搜索入口的站点会参与搜索，
选中的章节页或目录页会继续走原有下载流程。站点搜索遇到真人验证时，可以加上
`--browser`，在可见浏览器中完成正常操作；程序不会绕过验证。

注意 `23txxt.com` 和 `69shuba.com` 整个域名都受 Cloudflare WAF 保护，普通 HTTP
模式搜索时这两个站会显示"访问受阻"；要在搜索中包含它们，请使用 `--browser` 或
`--browser-connect` 在浏览器中人工完成验证（23txxt 的浏览器搜索会自动填表提交，
69shuba 需要手动点选 Turnstile 复选框）。其余站点若 0 结果则用宽松书名匹配过滤掉。

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

## 验证页自动切换浏览器

普通 HTTP 模式默认开启 `--browser-fallback`：当请求返回 Cloudflare 等
真人验证页时，程序会先询问是否打开可见浏览器。同意后浏览器窗口弹出，
你手动完成验证，脚本自动检测页面恢复并继续下载；后续所有请求复用这个
浏览器会话，不需要重启命令。程序不会自动破解验证，验证始终由你本人
完成。可以用 `--no-browser-fallback` 关闭该行为；非交互环境（管道、
脚本）中不会询问，仍按原样报错退出。

注意：69shuba 等站点会对连续高频请求周期性重新弹验证（实测约每
30~40 章一次）；验证通过后脚本会自动冷却 3 秒再继续。若触发仍然频繁，
可加大章节间隔 `--delay 2` 或 `--delay 3` 降低请求频率。

### 站点分级与静默降级

不同站点对自动请求的接受度不同：trxs.cc、笔仙阁等站点普通 HTTP 即可
访问；23txxt.com、69shuba.com 整域受 Cloudflare WAF/验证码保护。使用
浏览器模式时，脚本按站点自动分级：**无需验证的站点仍走普通 HTTP 静默
请求**（更快、不弹浏览器窗口、不占用验证等待），只有声明需要验证的
站点才动用浏览器。

需要浏览器模式的会话复用与 Chrome 手动连接方式，见下一节。

## 69shuba 站内搜索

69shuba 现在对整个域名做了 Cloudflare WAF 拦截，裸 `requests` 连首页
都收 403；站内搜索入口又叠加了一层站点级 Cloudflare Turnstile 控件，
必须在真实浏览器里通过验证换 `shuba` cookie 才能拿到结果。

普通 HTTP 搜索默认会让 trxs.cc / 笔仙阁 / 69shuba 并发请求，但 69shuba
会得到"访问受阻"，其余站点若 0 结果则用宽松书名匹配过滤掉。

要让 69shuba 搜索能用，需要让搜索走真实浏览器：

```powershell
python -m book_downloader --browser --search "以一龙之力打倒整个世界"
```

或者用 `--browser-connect` 接真实浏览器。不带地址时脚本会自动处理：
先探测 9222-9224 端口上已开的调试窗口（例如你之前手动启动的 Chrome），
有就复用；没有则自动启动默认浏览器（Chrome/Edge，独立配置目录，不影响
日常浏览），无需手动操作：

```powershell
python -m book_downloader --browser-connect --search "以一龙之力打倒整个世界"
```

也可以显式指定你自己开的 Chrome：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="D:\workspace\Projects\book-downloader\cache\normal-chrome"
python -m book_downloader --browser-connect http://127.0.0.1:9222 --search "以一龙之力打倒整个世界"
```

流程：脚本打开 69shuba 首页建立会话→ 填表提交搜索 → Cloudflare 可能
弹"请验证您是真人"复选框 → 在浏览器窗口里点一下 → 脚本自动等结果
出现并解析。Turnstile 验证超时用 `--verification-timeout` 控制，默认
180 秒。

注意：项目自己启动的 Chrome（`--browser`）带 `navigator.webdriver` 标记，
69shuba 在这种指纹下连 Turnstile 复选框都不会显示；如果搜索一直卡在
验证页面，请改用 `--browser-connect` 连你自己的 Chrome。

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

`--browser-connect` 也可以不带地址：脚本自动探测已开的调试端口并复用，
没有则自动启动默认浏览器，不需要手动操作。

## 站点适配

站点差异放在 `book_downloader/sites/`：

- `trxs_cc.py`：目录识别、正文选择、章节标题净化和简介清理
- `txxt.py`：23txxt 的目录排序、作者公告过滤、正文选择、分页标题/续页提示净化，以及 `_2.html` 续页 URL 规范化；站内搜索支持普通 HTTP 请求和浏览器页面级搜索（WAF 拦截时自动填表并等待人工验证）
- `bixiange.py`：笔仙阁镜像的 GBK 搜索、目录识别、正文选择、分段合并和首节简介截取
- `shuba.py`：69 书吧的 GBK 搜索、目录识别、正文选择和页面模板净化
- 站内搜索：由各适配器分别提供 `build_search_request` 和 `parse_search_results`；请求可以按站点声明 GET/POST、表单参数和响应编码，没有稳定公开搜索入口的站点会自动跳过
- `registry.py`：根据输入 URL 自动选择适配器

章节净化是站点级逻辑。以 `trxs.cc` 为例，输出会删除重复的“书名 + 第 N 章”页面标题，保留一份干净的章节标题，并从第 1 章正文前移除作者、简介、推荐语等书籍介绍。

对 23txxt，程序只把同一章节 URL 主干下的 `_2.html`、`_3.html` 视为续页，不会把下一章拼进当前章；合并时会删除站点名“二三书库”、`章节名 (第1/2页)` 等分页标题和“本章未完，请点击下一页继续阅读”提示。已有缓存也会重新应用当前净化规则。

对笔仙阁镜像，目录中的 `index/N.html` 是正文分段入口，不一定对应真实章节。合并时会根据正文中的真实章节标题重新分段，不会把 `第 N 节` 分段标签写入正文；分段缓存仍用于断点续传，已确认的站点污染行也会在重新合并时清除。

对 69 书吧，目录页使用 `/book/<id>/`，章节页使用 `/txt/<book-id>/<chapter-id>`；适配器会过滤日期、作者、重复标题、站点导航和“本章完”等页面模板。章节页可能触发站点验证时，程序仍会按现有规则停止，不绕过验证。

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
├─ fallback.py            # 验证页自动切换浏览器的客户端包装
├─ browser.py             # 可见浏览器与人工验证/连接模式
└─ sites/
   ├─ base.py             # 站点适配器基类
   ├─ common.py           # 通用目录/正文解析
   ├─ trxs_cc.py          # trxs.cc 适配器
   ├─ txxt.py             # 23txxt 适配器
   ├─ bixiange.py         # 笔仙阁镜像适配器
   ├─ shuba.py             # 69 书吧适配器（含页面级搜索）
   └─ registry.py         # URL 到适配器的选择
```
