# book-downloader

书页 Chrome 插件的焕新方案见 [焕新计划](docs/renewal.md)，工程样机安装与范围见 [样机说明](extension/README.md)。以下保留旧版使用说明。

从一个小说 URL 或已纳入站点的站内搜索结果自动发现目录、抓取章节并合并为 UTF-8 TXT。输入可以是目录页，也可以是任意章节页；程序会尝试从章节页找到目录链接，或者根据已知站点的 URL 结构推断目录页。

项目只请求公开 HTML，不处理登录、验证码、付费墙、隐藏下载接口或反爬验证。遇到 Cloudflare 真人验证时会停止并提示，不会模拟或绕过验证。


## 本地 Web 界面

首次准备可选依赖（Ubuntu-22.04 项目终端）：

```bash
uv pip install --python .venv/bin/python -e '.[web,browser]'
```

之后双击仓库中的 `启动书页.cmd`，或在项目终端执行：

```bash
.venv/bin/python -m book_downloader.web --open
```

页面地址为 <http://127.0.0.1:8765>，只监听本机。启动器会复用已运行的同项目后台，端口属于其他服务时明确报错，不会连接未知服务。Windows 桌面快捷方式可通过运行 `scripts/install-web-shortcut.ps1` 创建（右键使用 PowerShell 运行）；启动脚本与快捷方式使用本机约定的 Ubuntu-22.04 和项目路径。

- **书架**：更新目录并下载、补齐缺口、从缓存重新生成 TXT、下载 TXT。只有来源记录而无缓存目录时，先点击更新下载。
- **删除书籍**：书架点击“删除”，默认仅从 Web 书架移除并保留文件，重新添加同一链接可恢复。勾选“同时永久删除”才会清理确认框列出的缓存目录（含旧缓存）、TXT 和来源文件；任务历史保留，无法识别归属的文件不清理。若清理中途失败，书目会以“待清理”状态保留，重新确认后从已记录的范围继续；完成前不能重新下载该书。有排队、执行中或尚未确定目录目标的任务时，请先停止并等待结束。
- **添加下载**：粘贴目录或章节链接，或搜索书名关键词并选择结果。多站搜索先展示已找到的结果；可继续选书加入队列，也可结束剩余搜索并保留结果，随后执行下载。任务逐本排队，重复点击不会重复提交同一个活动任务。
- **任务中心**：查看当前进度、缺失项、自检告警和可展开详情。目录项/缓存项统计站点分页，合并后的真实章节数量另行展示。
- **人工验证**：默认使用当前 Chrome/Edge 的任务标签页。点击页面右上角“连接当前浏览器”，按说明安装本地扩展并授权一次；连接后搜索和验证自动在新标签页进行，手动验证后继续，关闭书页不停止任务。后台重启后刷新书页重新连接。可在高级设置切换为独立 WSL 浏览器；新启动的 WSL 浏览器会引用 Windows 已有中文字体。HTTP 403 等拒绝访问不统一认定为验证码。浏览器显示“不支持安全连接”等内部提示时，任务保持当前标签页等待，用户自行决定是否继续；等待可停止，超时后不会自动跳回首页。页面可区分未连接、未授权、旧版扩展与已连接，并提供“前往任务标签页”。
- **停止与恢复**：停止后不再启动下一请求；进行中的请求允许返回或超时，已完成缓存保留，有可用内容时生成部分 TXT。补齐重试和验证等待均响应停止。关闭网页不退出后台；停止整个后台使用页面左下角“退出后台”。服务重启后未完成任务标为中断/暂停，由用户点击重试，不自动恢复网络请求。

设置中的输出目录、间隔、超时、重试和浏览器路径保存在 `.local/web/settings.json`，任务记录与日志也位于 `.local/web/`。每个任务保存自己的设置快照，普通重试沿用原设置；保存的新设置与原任务不同时，可明确选择“按当前设置重试”，不会修改原任务记录。缓存继续使用项目 `cache/`，输出默认使用 `outputs/`，不会迁移现有缓存。不要从 CLI 和 Web 同时修改同一本书的缓存。

TXT 可能是部分结果：下载/补缺未完成和离线合并缺章都会在页面明确标注。损坏 UTF-8 缓存保留并提供备份/移走后补抓的建议。历史上如果目录 URL 已被覆盖为新地址、正文却仍是旧内容，现有元数据无法自动判别，需要按已知来源恢复或重新下载。

Web 测试需要可选测试依赖：

```bash
uv pip install --python .venv/bin/python -e '.[web,browser,test]'
python -m unittest discover -s tests -v
BOOK_DOWNLOADER_UI_TESTS=1 python -m unittest tests.test_web_ui tests.test_extension_ui -v
```

页面端到端测试使用临时目录与离线站点夹具，不访问真实书籍。CLI 本身不要求安装 Web 依赖；未安装时可只运行原有测试模块。

## 本机开发（WSL）

在 VS Code 的 Ubuntu-22.04 窗口中打开项目，使用 `.venv/bin/python`。安装依赖后激活环境，后文的普通 Python 命令在此终端执行：

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e '.[browser]'
source .venv/bin/activate
```

已有环境直接激活。F5 的默认入口为 `python -m book_downloader --list`，查看本地收录目录。
WSL 浏览器模式使用 Linux 浏览器。后文明确使用 Windows Chrome 的 PowerShell 示例，其浏览器和 Python 均在 Windows 中执行；两套环境的 localhost 和浏览器配置不共用。

## 协作入口

项目规则见 [AGENTS.md](AGENTS.md)。本机偏好与必要上下文位于 `.local/memory.md`（由 `AGENTS.md` 导入），不进入 Git。共享知识与具体需求记录以项目文档为准。

## 安装

```bash
uv pip install --python .venv/bin/python -r requirements.txt
```

## 使用

只需要提供一个 URL：

```bash
python -m book_downloader "https://www.trxs.cc/tongren/11699/147.html"
```

也可以传目录页：

```bash
python -m book_downloader "https://www.trxs.cc/tongren/11699.html"
```

也可以直接调用已纳入站点的公开站内搜索，再从结果中选择下载：

```bash
python -m book_downloader --search "示例小说"
```

程序会显示结果编号、站点、链接和摘要，然后等待输入编号。需要脚本模式时，
可以用 `--search-result 2` 直接选择第二条结果。搜索范围由站点适配器注册表生成，
当前纳入 `trxs.cc`、`23txxt.com`、笔仙阁镜像和 `69shuba.com`；其中只有提供稳定公开搜索入口的站点会参与搜索，
选中的章节页或目录页会继续走原有下载流程。站点搜索遇到真人验证时，可以加上
`--browser`，在可见浏览器中完成正常操作；程序不会绕过验证。

合并后的结果按相关度排序：标题与搜索词完全一致的排最前，其次是按前缀、
包含完整词匹配的，弱匹配与不相关的结果排在后面（多站点结果仍会交错，
避免单一站点占满前列）。

注意 `23txxt.com` 和 `69shuba.com` 整个域名都受 Cloudflare WAF 保护，普通 HTTP
模式搜索时这两个站会显示"访问受阻"；要在搜索中包含它们，请使用 `--browser` 或
`--browser-connect` 在浏览器中人工完成验证（23txxt 的浏览器搜索会自动填表提交，
69shuba 需要手动点选 Turnstile 复选框）。其余站点若 0 结果则用宽松书名匹配过滤掉。

如果关键词包含空格或标点，程序会使用最长的连续关键词请求，并对返回书名做去空格、
去标点匹配，以减少输入格式差异造成的漏搜；每个站点每次搜索只发送一次请求，避免触发站点频控。

输出默认写入 `outputs/<书名>.txt`，缓存默认写入 `cache/<站点>/<目录地址哈希>/`。重新运行同一本书时，已完成章节会自动复用；如果站点调整了目录顺序，程序会按章节 URL 对齐可复用缓存，并保留一份 `chapters.stale-*` 旧目录供恢复。输出 TXT 会根据当前目录和净化规则重新合并，不要求输出文件名固定。

下载过程中按 `Ctrl+C` 会停止后续请求，整理已经完成的缓存并生成部分 TXT；下一次运行相同命令即可继续。

下载完成时还会把目录 URL 写到输出文件旁边（`outputs/<书名>.url`）。这是与缓存解耦的更新锚点：即使缓存目录被清理，也能凭它找回更新入口。

## 已收录书目与更新

```bash
python -m book_downloader --list
```

列出缓存与来源文件中已收录的书目（书名、站点、目录/已缓存章节数），不发起网络请求。

```bash
python -m book_downloader --update "示例小说"
```

按书名关键词查找已收录的书（忽略空格和标点差异，完全一致的结果排最前；命中多本时列出编号供选择），然后重跑该书的下载流程：已完成章节继续使用缓存，只抓目录里新增的章节。查找来源是缓存 `book.json` 与输出目录的 `.url` 来源文件，同一目录 URL 两边都有记录时会合并展示。

## 离线重新合并

净化规则和章节分段规则更新后，不需要联网也能用现有缓存重新合并输出：

```bash
python -m book_downloader --update "示例小说" --merge-only
python -m book_downloader "https://www.trxs.cc/tongren/11699.html" --merge-only
```

`--merge-only` 不创建 HTTP 客户端、不发起任何网络请求，只用缓存里的目录记录和章节重新跑一遍净化与分段，再写出 TXT。适合站点限流（如 trxs.cc 高频后临时 403）时，先把手上的缓存合并成最新的结果。输入可以是目录页或章节页（会按适配器推断成目录地址）；缺失的缓存章节只提示不报错，去掉 `--merge-only` 重跑即可补齐。缓存里没有这本书的目录记录时会报错退出。

## 补齐缺口

```bash
python -m book_downloader --update "示例小说" --fill-gaps
python -m book_downloader "http://www.23txxt.com/bqg/111797/" --fill-gaps --delay 2
```

站点验证页或限流会让一次下载在中段停住，留下连续缺口。`--fill-gaps` 跳过已有缓存的章节、只请求缺失部分，失败时按轮次退避重试（浏览器模式下等待人工验证），因此可反复运行直到补齐，补齐后自动重新合并。相关参数：`--retry-delay`（每轮重试前的等待秒数，默认 30）、`--retry-rounds`（额外重试轮数，默认 2）。

## 合并自检

每次合并（下载、离线合并、补齐缺口）后都会自检章节编号连续性、重复标题、空章节和疑似残缺章节（正文少于 50 字），发现问题以“自检提示”打印到标准错误输出，不影响 TXT 生成。分段或净化规则改坏时，这些提示通常就是第一现场。

可选参数：

```bash
python -m book_downloader "https://www.trxs.cc/tongren/11699/147.html" --output ./outputs/11699.txt --delay 1 --timeout 20 --retries 3 --max-pages 50
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

```bash
python -m book_downloader --browser --search "示例小说"
```

或者用 `--browser-connect` 接真实浏览器。不带地址时脚本会自动处理：
先探测 9222-9224 端口上已开的调试窗口（例如你之前手动启动的 Chrome），
有就复用；没有则自动启动默认浏览器（Chrome/Edge，独立配置目录，不影响
日常浏览），无需手动操作：

```bash
python -m book_downloader --browser-connect --search "示例小说"
```

也可以显式指定你自己开的 Chrome：

```powershell
& "C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="$PWD\cache\normal-chrome"
python -m book_downloader --browser-connect http://127.0.0.1:9222 --search "示例小说"
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

```bash
uv pip install --python .venv/bin/python "playwright>=1.40"
```

运行（也适用于站内搜索模式）：

```bash
python -m book_downloader --browser "http://www.23txxt.com/bqg/111084/44601734_2.html" --output ./outputs/111084.txt
```

浏览器会话默认保存在 `cache/browser-profile/`，后续运行可复用。
自动发现会先从 `PATH` 中依次查找 `chrome`、`google-chrome`、
`google-chrome-stable`、`chromium`、`chromium-browser`、`microsoft-edge`，
随后查找 Windows 常见安装位置、已启动 Playwright 报告的 Chromium 路径，
最后查找 Linux/WSL 的 `~/.cache/ms-playwright/chromium-*/chrome-linux*/chrome`。
若没有自动找到浏览器，可用 `--browser-executable` 指定当前环境中浏览器
可执行文件的完整路径；该参数优先于自动查找。

如果 `--browser` 打开的浏览器一直停在安全验证页，说明站点可能识别出了
自动化启动方式。此时可以让你自己启动普通 Chrome，再让脚本连接它：

```powershell
$chrome = "C:\Program Files\Google\Chrome\Application\chrome.exe"
& $chrome --remote-debugging-port=9222 --user-data-dir="$PWD\cache\normal-chrome"
```

在这个 Chrome 窗口中打开链接并手动完成验证，然后保持窗口不关闭，另开
终端运行：

```bash
python -m book_downloader --browser-connect http://127.0.0.1:9222 "http://www.23txxt.com/bqg/111084/44601734_2.html" --output ./outputs/111084.txt
```

`--browser-connect` 也可以不带地址：脚本自动探测已开的调试端口并复用，
没有则自动启动默认浏览器，不需要手动操作。

连接时会跳过已有的 `chrome://`、`chrome-extension://`、`about:` 等非 HTTP(S)
页面，优先复用普通网页；没有则新建标签页。标签页关闭后也会按相同规则恢复。
自动启动浏览器时会带上 `--no-first-run --no-default-browser-check`，减少停在欢迎页的情况。

## 站点适配

站点差异放在 `book_downloader/sites/`：

- `trxs_cc.py`：目录识别、正文选择、章节标题净化和简介清理
- `txxt.py`：23txxt 的目录排序、作者公告过滤、正文选择、分页标题/续页提示净化，以及 `_2.html` 续页 URL 规范化；站内搜索支持普通 HTTP 请求和浏览器页面级搜索（WAF 拦截时自动填表并等待人工验证）
- `bixiange.py`：笔仙阁镜像的 GBK 搜索、目录识别、正文选择、分段合并和首节简介截取
- `shuba.py`：69 书吧的 GBK 搜索、目录识别、正文选择和页面模板净化
- 站内搜索：由各适配器分别提供 `build_search_request` 和 `parse_search_results`；请求可以按站点声明 GET/POST、表单参数和响应编码，没有稳定公开搜索入口的站点会自动跳过
- `registry.py`：根据输入 URL 自动选择适配器

章节净化是站点级逻辑。以 `trxs.cc` 为例，输出会删除重复的“书名 + 第 N 章”页面标题，保留一份干净的章节标题，并从第 1 章正文前移除作者、简介、推荐语等书籍介绍；目录里的光杆“第 N 节”编号标题会统一为章节页页眉使用的“第 N 章”，便于阅读器识别。

`trxs.cc` 的目录链接只是按字数切分的阅读分页，真实章节标题（“第N章 名称”）埋在正文里。合并时会按这些埋入标题重新分段：光杆分页并入当前真实章节；最后一个带名标题之后源站不再有章节信息，每个分页各自成章并接续编号；全书没有埋入标题时退化为按分页分章。

对 23txxt，程序只把同一章节 URL 主干下的 `_2.html`、`_3.html` 视为续页，不会把下一章拼进当前章；合并时会删除站点名“二三书库”、`章节名 (第1/2页)` 等分页标题和“本章未完，请点击下一页继续阅读”提示。已有缓存也会重新应用当前净化规则。

对笔仙阁镜像，目录中的 `index/N.html` 是正文分段入口，不一定对应真实章节。合并时会根据正文中的真实章节标题重新分段，不会把 `第 N 节` 分段标签写入正文；分段缓存仍用于断点续传，已确认的站点污染行也会在重新合并时清除。

对 69 书吧，目录页使用 `/book/<id>/`，章节页使用 `/txt/<book-id>/<chapter-id>`；适配器会过滤日期、作者、重复标题、站点导航和“本章完”等页面模板。章节页可能触发站点验证时，程序仍会按现有规则停止，不绕过验证。

## 测试

```bash
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
