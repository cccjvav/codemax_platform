# Windows 新手逐步验收

**只按你的环境写：Windows＋VS Code 集成 CMD＋Conda 管 Python＋系统安装的 Node.js。**

这不是已经替你在 Windows 验收通过的报告，而是一条从头走到尾的操作路线。先跑通免费、本地、模拟支付的功能，再接真实模型，最后跑自动化测试。暂时不部署公网、不接真实微信付款、不装动态爬虫浏览器。

每个编号做完再继续。代码框中一行一行执行，**不要复制提示符 `C:\...>`、`(codemax)` 或说明文字**。任何红字/失败先停在当前步，不要靠重复初始化数据库解决。

## 0. 先认清三个地方

| 名称 | 你在哪里操作 | 用途 |
| --- | --- | --- |
| 终端 A「操作」 | VS Code → 终端 → 新建终端，选择 Command Prompt | 安装、配置、生成文档、模型探测 |
| 终端 B「网站」 | 同一个 VS Code 再新建一个 CMD 终端 | 持续运行网站；运行时不会回到命令提示符，这是正常的 |
| 第二个 VS Code 窗口「自动化验收」 | 第 14 步才创建；打开另一份无 `.env` 的代码 | 自动测试，避免误用开发数据库/真实模型 |

浏览器不是终端；`psql` 数据库交互窗口也不是 CMD。本文会在需要切换时明确说。

两个目录不要混用：

- `C:\work\codemax_platform`：本地演示代码，允许有私有 `.env`。
- `C:\work\codemax_acceptance`：干净自动验收代码，不放 `.env`。

上面是示例路径；你的项目若在别处，只替换目录路径。两个目录要是同一提交。先在仓库外建一个证据目录，用来写验收笔记：

```cmd
if not exist C:\work\codemax-evidence mkdir C:\work\codemax-evidence
```

**通过标准：**目录存在。笔记不包含密码、API key、Cookie、token、完整签名下载地址；不要直接上传 HAR/整份环境变量。

## 1. 将 VS Code 集成终端设为 CMD

1. 打开 VS Code。
2. 按 `Ctrl+Shift+P`，搜索 `Terminal: Select Default Profile`（终端：选择默认配置文件）。
3. 选 **Command Prompt / 命令提示符**，不是 PowerShell、Git Bash 或 WSL。
4. 关闭旧终端，再点菜单「终端 → 新建终端」。可右键终端标签重命名为「操作」。

**你应该看到：**类似 `C:\Users\你的名字>`；不应以 `PS C:\...>` 开头。

如果电脑还没有工具，先分别从官方安装：

- [Miniconda](https://www.anaconda.com/docs/getting-started/miniconda/install)：管理 Python；已有 Anaconda/Miniconda 不重复安装。
- [Git for Windows](https://git-scm.com/downloads/win)。
- [Node.js](https://nodejs.org/en/download)：建议 Node 24 LTS；已有 Node 22 且至少 22.12 也可使用。不需要在 Conda 里再装一份 Node。
- [PostgreSQL 16](https://www.postgresql.org/download/windows/)：保留 Server 和命令行工具，记住 `postgres` 账号密码与端口，本文示例用 5432。

**刚装完 Node/Git/Conda？必须完整退出所有 VS Code 窗口后重开。** 只关终端标签可能仍继承旧 PATH，导致明明装好了却提示命令不存在。

## 2. 确认 Conda 与系统 Node 都能找到

在终端 A 执行：

```cmd
conda --version
conda env list
where node
node --version
npm --version
where git
git --version
```

**通过标准：**每个命令都有版本/路径；Node 至少 22.12（或 24）；`where node` 首个结果是你打算使用的系统 Node，例如 `C:\Program Files\nodejs\node.exe`。

### 如果只有 conda 不认识

1. 在 Windows 开始菜单打开 **Miniconda Prompt / Anaconda Prompt**。
2. 在那个 Prompt 执行：

```cmd
conda init cmd.exe
```

3. 完整退出 VS Code，再重新打开、新建 CMD 终端，重试 `conda --version`。

这一步初始化 CMD，不是安装项目。若系统策略禁用 CMD AutoRun，初始化后仍不识别，可在已有 Conda Prompt 中执行 `conda activate codemax` 后从该窗口启动 `code`；已有环境与项目路径先按后面的步骤确认。不要关闭系统安全策略，也不要把网上猜来的 Conda 路径全局塞进 PATH。

### 如果 node/npm 不认识

先完整重启 VS Code，再重试。若 `where node` 仍找不到，检查系统 Node 安装时是否启用 PATH。不要用 `pip install node`，也不要通过更换 Python 环境来修 npm。

## 3. 准备 Python 3.11 环境

先看上一步 `conda env list`：

- **已有专用 `codemax` 环境**：只运行下面的激活和检查。
- **没有 `codemax` 环境**：先创建一次：

```cmd
conda create -n codemax python=3.11 pip -y
```

然后执行：

```cmd
conda activate codemax
set PYTHONUTF8=1
python --version
python -c "import sys; print(sys.executable)"
python -m pip --version
where python
```

**通过标准：**Python 显示 3.11.x，解释器与 pip 位于同一个 Conda 环境。若已有环境不是 3.11，不要强行降级混用环境，另建独立环境并在后文统一替换环境名。

不要激活 `.venv`，不要用 `py -m pip`；后面都用 `python -m pip`。`set PYTHONUTF8=1` 只影响当前终端的新 Python 进程，**每个新终端都重新执行**。

VS Code 编辑器也按 `Ctrl+Shift+P` → `Python: Select Interpreter` 选择这个 Conda 解释器。编辑器选对了不代表终端自动激活，仍以刚才的输出为准。

## 4. 打开正确代码，确认没有覆盖自己的修改

**已有本项目且在本会话分支上**，在终端 A：

```cmd
cd /d C:\work\codemax_platform
git status --short
git branch --show-current
```

应在 `arena/01a08bf5-codemax-platform`。如果有修改或在其他分支，先保留原目录，不执行 reset/clean/强制切分支；用下面的方式克隆到一个**尚不存在**的新目录，并把后文路径一起替换。

**尚未克隆**时，在终端 A：

```cmd
if not exist C:\work mkdir C:\work
git clone -c core.autocrlf=false --branch arena/01a08bf5-codemax-platform --single-branch https://github.com/cccjvav/codemax_platform.git C:\work\codemax_platform
cd /d C:\work\codemax_platform
```

如果是已有干净、正确分支的目录，可以更新：

```cmd
git pull --ff-only origin arena/01a08bf5-codemax-platform
```

失败就停止，不改用强制覆盖。然后在 VS Code 菜单「文件 → 打开文件夹」选择实际项目目录；重新打开文件夹后若终端变化，重复第 3 步激活。

```cmd
cd /d C:\work\codemax_platform
dir main.py
dir requirements.txt
git rev-parse HEAD
```

**通过标准：**两个文件存在，记下完整 SHA。VS Code 编辑中文文件时保持 **UTF-8 / LF**。本项目讲解校验源文件字节，CRLF 变化会导致指纹不符，不用 `--write` 批量掩盖它。

## 5. 安装 Python 依赖和前端依赖

终端 A，项目根目录，确认 `(codemax)` 后：

```cmd
python -m pip install -r requirements.txt
```

等结束，立即执行：

```cmd
echo %ERRORLEVEL%
```

看到 `0` 再继续；如果失败，保留最后的错误段并排查，不继续下一个安装掩盖它。

```cmd
python -m pip check
npm ci
```

`pip check` 应显示 `No broken requirements found`；`npm ci` 结束后立即 `echo %ERRORLEVEL%`，应为 0。接着：

```cmd
npm run build
```

同样检查退出码 0。Vite 大 chunk 的黄色提醒不是失败；不要执行 `npm audit fix --force` 随意换依赖。

**说明：**Node 只负责这里的构建与后面的前端测试。本项目不需要另启动一个 Node 网站服务。`npm run build` 与文档构建必须先后执行，不要在两个终端同时跑，因为 Vite 会清空再写静态目录。

## 6. 先打开文档站，获得一个不依赖数据库的成功结果

终端 A 逐项执行：

```cmd
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
start "" "docs\site\index.html"
```

每个 Python 命令立即检查 `echo %ERRORLEVEL%`，必须是 0。

**通过标准：**浏览器能打开文档；搜索“Windows 新手”，能找到本篇；点“精读覆盖与缺口”，进入一个源码页，能读到解释、代码并跳转行号。`--data-only` 也需要已安装的 Mistune，并不是免依赖模式。

若报告 stale hash/缺文件，先检查是否选错分支、源码被编辑成 CRLF 或构建未结束。不要为“看起来全绿”直接刷新指纹。记录错误即可继续排查；这时还不涉及 PostgreSQL。

## 7. 创建本次专用演示数据库

**此步只创建新的演示库 `codemax_walkthrough`，不是自动测试库，也不是你的旧项目数据库。**

先按 `Win+R`，输入 `services.msc`，确认 PostgreSQL 服务正在运行。终端 A：

```cmd
psql --version
```

若不认识 psql，先在资源管理器确认实际安装路径。安装在默认 PostgreSQL 16 目录时，可只为当前 CMD 设置：

```cmd
set "PATH=C:\Program Files\PostgreSQL\16\bin;%PATH%"
psql --version
```

不是该路径就替换为实际路径；这不会永久修改系统 PATH，新终端如需 psql 要再设置。

```cmd
psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -W -c "SELECT version();"
```

出现密码提示时输入 PostgreSQL 安装时的密码，**没有星号/回显是正常的**。应返回数据库版本；若进入分页画面，按 `q` 返回 CMD。

连接成功后只执行一次：

```cmd
createdb -h 127.0.0.1 -p 5432 -U postgres -W codemax_walkthrough
```

紧接着：

```cmd
echo %ERRORLEVEL%
```

**只有这次创建退出码为 0，才允许执行下一条。** 若报 already exists，停下核对旧库用途，不删除它、不继续执行本指南的新库init步骤；可以另选一个从未用过的库名，并同步替换本节和第 8 节。

空数据库创建到这里即可。**先完成下一节私有配置，再使用维护CLI建表**；不再直接运行full_init.sql，不预置admin/123456。

## 8. 配置私有 .env 和测试商品

终端 A：

```cmd
if not exist .env copy .env.example .env
```

在 VS Code 左侧打开 `.env`，把**同名行改成下面的值，不重复追加第二份同名设置**。若已有别的项目配置，先备份到仓库外，别覆盖真实凭证/文件路径。

```dotenv
ENV=development
DB_HOST=127.0.0.1
DB_PORT=5432
DB_NAME=codemax_walkthrough
DB_USER=postgres
DB_PASSWORD="在本机填写你的PostgreSQL密码"
DATABASE_URL=
SITE_BASE_URL=http://127.0.0.1:8000
SHOP_PAY_MODE=mock
TRUST_PROXY_HEADERS=false
STORAGE_BACKEND=local
STORAGE_LOCAL_ROOT=storage
STORAGE_PRODUCT_KEY=acceptance/demo.zip
DOWNLOAD_URL_TTL=300
LLM_API_KEY=
```

密码行的中文只是说明，请本地替换。双引号帮助保留 `#` 等字符；密码若含双引号或反斜线，按 dotenv 的双引号转义规则写，不要省略而改变实际密码。端口不是 5432 时也要同步修改。

初次先让 `LLM_API_KEY` 留空，先验不用模型的功能，**不是不允许使用你的 key**；第 13 步再启用。

生成本地 `SECRET_KEY`：

```cmd
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

将输出复制到 `.env` 的 `SECRET_KEY=` 后，`Ctrl+S` 保存，不截图分享。确认私有配置被忽略：

```cmd
git check-ignore .env
git ls-files -- .env
```

第一条应输出 `.env`，第二条应没有输出；若被跟踪，先停下处理，不提交它。系统/Conda 的同名环境变量优先于文件，不用 `set` 打印整份环境来排查密钥。

现在仍在项目根目录、Conda环境中，显式初始化刚才的空库：

```cmd
python "database init/db_init.py" init --confirm-database codemax_walkthrough
echo %ERRORLEVEL%
```

必须为0才继续；失败先停下核对，不清库、不改跑历史脚本。初始化拒绝任何已有表，旧版full_init曾会删表，不能退回旧版执行。

创建管理员，记住自己设置的口令（两次输入不回显，至少12字符；不要发到聊天）：

```cmd
python "database init/db_init.py" bootstrap-admin --username owner --confirm-database codemax_walkthrough
echo %ERRORLEVEL%
```

再次必须为0。`owner`若已被占用不能直接提权，先核对库是否是刚才新建的专用库。最后验证迁移状态：

```cmd
python "database init/db_init.py" status --confirm-database codemax_walkthrough
echo %ERRORLEVEL%
```

应显示Pending versions: none且退出0。已有0008旧库走[数据库指南](database%20init/README.md)的接入路径，本节不用于升级旧库。

再创建一个很小的测试 ZIP（不会覆盖已有同名文件）：

```cmd
python -c "from pathlib import Path; from zipfile import ZipFile; p=Path('storage/acceptance/demo.zip'); assert not p.exists(), 'Test file already exists; do not overwrite'; p.parent.mkdir(parents=True, exist_ok=True); z=ZipFile(p, 'w'); z.writestr('README.txt', 'CodeMax acceptance test file'); z.close()"
```

**通过标准：**`storage\acceptance\demo.zip` 存在。若提示已存在，只核对它是否你之前创建的测试包，不删除真实商品再重建。

## 9. 在终端 B 启动网站

新建一个 **CMD** 终端并重命名「网站」，完整执行：

```cmd
conda activate codemax
set PYTHONUTF8=1
cd /d C:\work\codemax_platform
```

这个新终端可能继承 Conda/系统配置；先清除下列**当前进程变量**，让刚保存的私有 `.env` 生效（不会删除 `.env` 或永久更改 Conda）：

```cmd
set ENV=
set DATABASE_URL=
set DB_HOST=
set DB_PORT=
set DB_NAME=
set DB_USER=
set DB_PASSWORD=
set SHOP_PAY_MODE=
set STORAGE_BACKEND=
set STORAGE_LOCAL_ROOT=
set STORAGE_PRODUCT_KEY=
set DOWNLOAD_URL_TTL=
set SITE_BASE_URL=
set TRUST_PROXY_HEADERS=
set SECRET_KEY=
set LLM_API_KEY=
set LLM_BASE_URL=
set LLM_MODEL=
set LLM_EMBED_ENABLED=
set LLM_EMBED_MODEL=
python -c "from app.config import settings as s; print(s.ENV, s.DB_HOST, s.DB_PORT, s.DB_NAME, s.SHOP_PAY_MODE); assert not s.DATABASE_URL and s.ENV == 'development' and s.DB_HOST == '127.0.0.1' and s.DB_NAME == 'codemax_walkthrough' and s.SHOP_PAY_MODE == 'mock', 'STOP: check private demo database configuration'"
```

核对输出为 development、127.0.0.1、实际端口、你的演示库名、mock，无 AssertionError 后才启动。若前面换了新库名，校验命令里的库名也要同步；不打印数据库密码或完整连接串。

```cmd
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

**通过标准：**看到启动完成和 `http://127.0.0.1:8000`；窗口持续显示日志，不回到提示符是正常的。不要在这个正在运行的终端继续粘贴安装命令。

回终端 A：

```cmd
curl.exe --fail http://127.0.0.1:8000/healthz
curl.exe --fail http://127.0.0.1:8000/readyz
start "" "http://127.0.0.1:8000/"
```

应分别看到 `status=ok`、`status=ready` 的 JSON，并打开首页。healthz 成功但 readyz 失败：进程活着，数据库未就绪，检查第 7～8 步，而不是重建数据库。

端口占用时只查看：

```cmd
netstat -ano | findstr :8000
```

若是你之前开的本项目，回对应终端按 `Ctrl+C`；不要按未知 PID 强杀其他程序。没确认占用者前先不继续。

## 10. 验收登录、ER 与 Word

### 10.1 创建客户 A

1. 首页点「登录 / 注册」→「注册」。
2. 用户名用 `learner_a`，密码自行设置并记好，不用示例明文当正式密码。
3. 注册/登录成功后，顶栏应显示这个用户。
4. 刷新首页，登录态仍应存在；点击退出后用户显示消失，再登录回来。

已存在同名用户时登录已有账号或选新名，不删除用户表。

### 10.2 ER 与 Word

打开 `http://127.0.0.1:8000/tools/er`，把下面内容放进输入框：

```sql
CREATE TABLE customer (
    id INT PRIMARY KEY,
    name VARCHAR(40) NOT NULL
);
CREATE TABLE purchase (
    id INT PRIMARY KEY,
    customer_id INT REFERENCES customer(id)
);
```

1. 点「生成 ER 图」，应有两张表和外键连线，能缩放拖动。
2. 点「导出 Word」，下载 `data_dictionary.docx`。
3. 用桌面 Word 打开，应无“修复文件”警告，字段、主键和外键对应输入。

没有 Word 时记录“文件下载通过、桌面 Word 显示未执行”，不要把下载成功当作显示验收通过。

## 11. 用两个浏览器验收私人图与站内客服

**普通标签页共享 Cookie。** 建议 Edge 普通窗口作客户 A，Chrome 普通窗口作管理员；另一个浏览器配置文件作客户 B。多个无痕窗口也可能共用同一个无痕会话，不将它们直接视作独立账号。

### 11.1 Drawio：外部编辑器需要联网

1. A 打开 `http://127.0.0.1:8000/tools/drawio`。
2. 等编辑器加载，在图里写 `验收 A-1`，填流程图名称，点「保存到云端」。
3. 改成 `验收 A-2` 后立即保存，再从列表打开，应显示 A-2。
4. 点「下载 .drawio」，再导入刚下载的文件，核对文字。
5. 展开「管理云端文件 / 回收站」，点刷新，对**测试图**执行移至回收站→恢复；确认两边列表变化。
6. 若测试永久删除，只删不再需要的测试图，确认后应不可恢复。
7. 同账号开两页，各自打开同一云图。第一页改后保存，第二页再改并保存，应提示版本冲突，不能无声覆盖。

编辑器无法加载或 10 秒导出无响应，先记“外部编辑器受阻”。不要安装 Playwright 来解决它：本站 Drawio iframe 与已停用的动态爬虫不是同一件事。

### 11.2 管理员首次登录与改密

在 Chrome 打开首页，用第8步创建的 `owner` 和你设置的口令登录，仅操作这次本地专用库。随后：

1. 同一 Chrome 窗口打开 `http://127.0.0.1:8000/docs`。
2. 找 `POST /auth/password`，展开 → `Try it out`。
3. 在请求 JSON 中本地填 `old_password` 和自己选择的 `new_password`，点 `Execute`。
4. 期望 200；响应中的 token 不截图。Cookie 会更新。
5. 若 Swagger 之前点过 `Authorize` 留了旧 Bearer，先在该弹窗 `Logout` 清旧授权；否则旧 Bearer 会优先于新 Cookie。
6. 回首页退出，用新密码重新登录。

这里使用同源浏览器 Cookie，不必复制 token 到命令行。遇到 401 先确认是在管理员已登录的同一浏览器；不要关闭后端鉴权。生产不会开放 `/docs`，本文不是生产管理方案。

### 11.3 客户—管理员来回留言

1. Edge 的 A 打开 `http://127.0.0.1:8000/support/center`，发送 `验收消息 A-1，请管理员回复`。
2. Chrome 的管理员打开同一地址，点「刷新会话」，选 A 的会话，回复 `已收到 A-1`。
3. 回 Edge，等待页面轮询，应该看到回复；刷新后历史仍保留。
4. 用独立浏览器配置文件注册 `learner_b`，B 的客服页不应显示 A 的留言，B 的云图/订单列表也应独立。
5. 管理员回到客户会话列表，能区分 A/B；不要往消息里放真实密码或 key。

**通过标准：**双方能继续沟通、刷新保留、账号隔离。智能问答返回“人工入口”不会自动代替你发送这条留言，也没有自动邮件/手机通知。

## 12. 验收模拟付款与可重领下载

确认 `.env` 是 `SHOP_PAY_MODE=mock`，**不需要扫码或真实付款**。

1. A 打开 `http://127.0.0.1:8000/shop`，点「立即购买」。
2. 在待付区域点「前往收银台支付」，应看到醒目的“模拟支付”警告。
3. 点「模拟支付成功」，然后返回商城，查看/刷新我的订单，选择该单。
4. 点「下载交付物」。应下载 ZIP，打开其中 README.txt，内容为 `CodeMax acceptance test file`。
5. 再回同一订单点下载，应该还能下载；已领过链接不等于永久消耗权益。
6. 换 B 看订单，不能看到 A 的订单；刷新/查看历史不自动产生新订单。

要进一步验证链接过期：终端 B 按 Ctrl+C，`.env` 暂设 `DOWNLOAD_URL_TTL=10`，保存后重跑第 9 步启动命令。领取一次链接，只在本机浏览器历史/下载记录里找到最终 `/shop/dl?...` 地址，等超过 10 秒后重开旧地址，应拒绝；回订单重领的新链接应成功。不要把完整地址截图分享。测试完将 TTL 恢复 300 并重启。

若小 ZIP 瞬间下载完成，不能因此说测过“传输中断恢复”；该项可记未执行。`manual` 人工核账和 `wechat` 商户付款不在本次免费模拟流程里，不为验收而确认未到账的真实单。

## 13. 使用你的 API key 做实际模型验收

**本节才会向外部提供方发请求，可能消耗免费额度。** 每个探测命令只发一次请求，无自动重试；只发送脚本内固定的简单示例，不上传源码/真实客户资料。密钥不作为命令行参数，不放进 Git。

当前 Agnes 配置与网络诊断进展见 [Agnes 接入说明](docs/AGNES_AI.md)。下列为历史探测边界，不代表已完成鉴权：

2026-09-13 远端探测说明：已获用户授权并尝试使用所给 key 访问 `/v1/models`，但该沙箱对提供方发生 TLS 握手断开，未取得 HTTP 响应，无法判断 key 或模型权限。网页工具能访问不等于 Python 能连通。没有据此宣称聊天/embedding不受支持，亦未将它们记为通过；记录见[本轮状态](manager/stages/windows-acceptance.md)。你的本机可能有不同结果，按下面实际执行。

### 13.1 配置 Agnes，先做无密钥连通性诊断

在应用目录的私有 `.env` 修改：

```dotenv
LLM_API_KEY=在本机粘贴你授权使用的key
LLM_BASE_URL=https://apihub.agnes-ai.com/v1
LLM_MODEL=agnes-2.5-flash
LLM_EMBED_ENABLED=false
LLM_EMBED_MODEL=
```

中文密钥占位符必须替换；聊天模型使用官方 ID `agnes-2.5-flash`，embedding 默认关闭并留空，不猜向量模型名。保存后，终端 A 先清除本终端可能继承的同名变量，确保读取你刚写的私有 `.env`：

```cmd
set LLM_API_KEY=
set LLM_BASE_URL=
set LLM_MODEL=
set LLM_EMBED_ENABLED=
set LLM_EMBED_MODEL=
```

再执行：

```cmd
python scripts/probe_llm.py connectivity
echo %ERRORLEVEL%
```

应退出 0 并显示 `HTTP REACHED` 和 HTTP 状态码。该模式不发送 key，401/403/404/503 都只证明已得到 HTTP 响应，不证明鉴权或模型通过。若 TLS/ConnectError，先处理网络路径，不重装数据库或换模型来修 TLS。

模型列表可选运行 `python scripts/probe_llm.py models`；已知官方模型 ID 时，**不要求 models 成功才测 chat**。若 models 返回 404 而 chat 正常，不把聊天判为失败。

若提示 `PROBE FAILED`，先查看本节末尾排错表；脚本刻意不回显服务端错误正文，防止其意外带回 key。不要加 `verify=False`、`curl -k` 或把 key 放 URL 查询参数。

### 13.2 聊天与 Mermaid 分开确认

Agnes 官方 Chat Completions 的模型 ID 已确认为 `agnes-2.5-flash`，上一步已填好。不要保留旧 `gpt-4o-mini`，也不要切到付费 Pro 来碰运气。现在逐个执行：

```cmd
python scripts/probe_llm.py chat
echo %ERRORLEVEL%
python scripts/probe_llm.py mermaid
echo %ERRORLEVEL%
```

每个退出码必须分别看，不因后一条成功掩盖前一条失败。

- `CHAT PASS`：本项目 LLMClient 收到了合法非空文本，不承诺回复一定正确。
- `MERMAID PREFIX PASS`：返回通过项目的 Mermaid 前缀检查，**还不是浏览器渲染通过**。

终端 B 先 Ctrl+C，再重新运行第 9 步启动命令，确保新配置生效。浏览器打开 `http://127.0.0.1:8000/tools/mermaid`，输入“一个客户有多个订单”，点击生成，确认图与源文本出现、没有渲染错误。

再在 `/docs` 的 `POST /support/ask` → Try it out，输入 `{"text":"你好"}` 并 Execute：检查 answer/source/escalated，不只看 HTTP 200。FAQ 固定答案可能完全不调用模型；回人工入口说明发生了降级，不能当模型联调通过。

### 13.3 embedding 只有确认支持时再测

Agnes 当前公开文档没有确认向量模型，本次默认**跳过本节及 13.4**，词袋 FAQ 和聊天仍可用。未来只有提供方明确支持同一基址/账号的 `/embeddings` 并给出具体 ID，才同时设 `LLM_EMBED_ENABLED=true` 和 `LLM_EMBED_MODEL=实际ID`，再执行：

```cmd
python scripts/probe_llm.py embeddings
echo %ERRORLEVEL%
```

应退出 0，得到两条同维有限数值向量的说明。脚本不打印向量正文。不支持就记录“该提供方的 embedding 未启用/待确认”，保留词袋 FAQ；不要把聊天模型名硬塞给 embedding 来凑通过。

### 13.4 向量探测通过后，再标定语义 FAQ

本步会发多次 embedding 请求并耗额度，只有上一步成功且愿意继续才运行。在**有 `.env` 的应用目录**只跑这一条真实模型测试：

```cmd
python -c "from dotenv import load_dotenv; load_dotenv(); import pytest; raise SystemExit(pytest.main(['tests/test_faq_semantic.py::test_calibrate_semantic_threshold', '-q', '-s', '-rs']))"
```

检查退出码、实际执行数和阈值结果。`skipped` 不算通过；模型把同义问句匹配到错误 FAQ，或正负分布重叠，不能只降低阈值。确有可分区间时才按结果更新 `.env` 的 `LLM_SEMANTIC_THRESHOLD`，重启并复测。

**不要在这个带真实 key 的目录运行完整默认测试套件。** 第 14 步会提供干净副本。

### 模型失败怎么判断

| 现象 | 先做什么 | 不能据此认定什么 |
| --- | --- | --- |
| `ConnectError` / TLS / SSL EOF | 检查这台机器能否访问提供方；保留异常类型，不关闭证书校验 | 不是已验证的 key 无效，也不是模型必然不支持 |
| `TimeoutException` | 稍后人工重试一次，核提供方状态；不要无限循环耗额度 | 不是项目所有功能都坏了 |
| `ValueError` / `configuration` | 查 key/HTTPS基址/模式对应模型ID是否填好；模型列表可能非兼容JSON | 不是靠换数据库解决 |
| `http HTTP 401/402/403/404/429/5xx` | 依次核对 key、余额/权限、模型/端点、限频及服务状态；不打印错误正文 | 不应打印 Authorization 或原始响应到公开日志 |
| 本机能打开登录网页，Python仍连接失败 | 分开记录网页地址、API地址和进程出站结果 | 网页通不保证API通，更不保证embedding可用 |

## 14. 在干净副本运行自动化验收

此时可以先停终端 B 的网站，避免同时跑重计算影响本机测试时间。

在终端 A，克隆到**尚不存在**的验收目录：

```cmd
git clone -c core.autocrlf=false --branch arena/01a08bf5-codemax-platform --single-branch https://github.com/cccjvav/codemax_platform.git C:\work\codemax_acceptance
```

若已经有这个目录，先核对其用途和改动，不删除重建。用 VS Code「文件 → 新建窗口 → 打开文件夹」打开 `C:\work\codemax_acceptance`，在新窗口建 CMD 终端：

```cmd
conda activate codemax
set PYTHONUTF8=1
cd /d C:\work\codemax_acceptance
git rev-parse HEAD
if exist .env echo STOP: this checkout must not contain .env
```

SHA 必须与第 4 步一致；有 `.env` 就停下，不在这里启动自动测试。项目私有配置不要复制过来。清除当前终端可能继承的业务选择：

```cmd
set TEST_DATABASE_URL=
set DATABASE_URL=
set ENV=
set SHOP_PAY_MODE=
set STORAGE_BACKEND=
set STORAGE_LOCAL_ROOT=
set STORAGE_PRODUCT_KEY=
set DOWNLOAD_URL_TTL=
set SITE_BASE_URL=
set TRUST_PROXY_HEADERS=
set SECRET_KEY=
set LLM_API_KEY=
set LLM_BASE_URL=
set LLM_MODEL=
set LLM_EMBED_ENABLED=
set LLM_EMBED_MODEL=
set LLM_SEMANTIC_THRESHOLD=
```

Conda 若持久配置过其他业务变量也会影响测试；不要把环境变量全集贴出来。这个新终端里的清空不会删原项目 `.env`，也不会更改另一个终端。

依次执行，每项结束检查退出码 0：

```cmd
python -m pip install -r requirements.txt
python -m pip check
npm ci
python -m ruff check .
python -m pytest -q -rs
```

全量测试需要几分钟；没有实时一行一行日志不一定卡住。应无 failed/error；真实 PG/embedding 场景在 SQLite 下有注明原因的 skipped，不要删除断言追求“零跳过”。

继续逐项：

```cmd
npm run build
git diff --exit-code -- app/static/js
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
```

**通过标准：**构建成功、bundle 无差异、README/精读/链接门禁通过。若版本、环境和 SHA 不同，不拿旧文档里的固定测试数硬比。

## 15. 再用专用 PostgreSQL 测试库补并发/迁移

仍在**干净验收目录**，需要第 7 步的 psql 命令可用。本节库名是 `codemax_test`，**绝不是 `codemax_walkthrough`**。

在 CMD 执行：

```cmd
psql -h 127.0.0.1 -p 5432 -U postgres -d postgres -W
```

现在应看到 `postgres=#`。**下面只在 psql 提示符内逐行执行**，先查询是否已经存在：

```sql
SELECT rolname FROM pg_roles WHERE rolname = 'codemax_test';
SELECT datname FROM pg_database WHERE datname = 'codemax_test';
```

如果任意查询返回记录，先核对是不是已经存在的专用可丢弃测试资源，不删除、不擅自改密码；无法确认就输入 `\q` 退出，暂停此步。只有两者均不存在时创建：

```sql
CREATE ROLE codemax_test LOGIN;
\password codemax_test
CREATE DATABASE codemax_test OWNER codemax_test;
\q
```

`\password` 会要求两次输入新测试密码，不需要超级用户权限给这个测试角色。回到 CMD 后：

```cmd
python -c "import os, getpass, pytest; from sqlalchemy.engine import URL; os.environ['TEST_DATABASE_URL']=URL.create('postgresql+asyncpg', username='codemax_test', password=getpass.getpass('Test DB password: '), host='127.0.0.1', port=5432, database='codemax_test').render_as_string(hide_password=False); raise SystemExit(pytest.main(['-q', '-rs']))"
```

密码在提示中输入，不回显，不写到命令行。**fixture 会创建/删除此库内的项目表**；专用测试库不用先跑 full_init。测试成功应没有 failed/error；真实 embedding 标定仍可能跳过，由第 13 步单独处理。

## 16. 停止、明天再启动，以及填写结果

网站在终端 B 按 `Ctrl+C` 停止。只是关闭浏览器不会停后端，`conda deactivate` 也不替你停止其他终端的进程。

下次重新打开**应用目录**，在 CMD 先激活并定位：

```cmd
conda activate codemax
set PYTHONUTF8=1
cd /d C:\work\codemax_platform
```

新终端仍要按第 9 步清除同名进程变量、核对非敏感演示库配置，再启动：

```cmd
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

**不重复初始化数据库、不重新造商品、不删除卷。** 只改 `.env` 时停止后重启；改前端才重新 `npm run build`。

把下表复制到仓库外的验收笔记，未做的保留“未执行”：

| 项目 | 状态：通过/失败/未执行/不适用 | 证据或原因 |
| --- | --- | --- |
| 日期、Git SHA、Windows/Conda/Python/Node版本 | 待填 | 不粘贴环境变量全集 |
| CMD与正确解释器、安装/构建 | 未执行 | |
| 文档导航/搜索/源码精读 | 未执行 | |
| 数据库就绪、登录/退出 | 未执行 | |
| ER与Word桌面显示 | 未执行 | |
| Drawio即时保存/冲突/回收站 | 未执行 | |
| 客户与管理员留言、账号隔离 | 未执行 | |
| 模拟付款、ZIP、过期后重领 | 未执行 | |
| 真实模型聊天/Mermaid渲染 | 未执行 | 填供应商和模型ID，不填key |
| embedding与语义阈值 | 未执行 | 不支持与未测试要区分 |
| SQLite、PostgreSQL、文档/前端门禁 | 未执行 | 分别记通过数和跳过原因 |
| 当前 SHA 的六项 GitHub CI | 未执行 | CI通过不等于本机通过 |

有失败时反馈：**第几步、哪条命令/哪个按钮、实际状态码或错误类型、已脱敏截图**。不要只说“不能用”，也不要发送密码/key/完整下载地址。

这份表全部完成的是所选范围的**学习与本地验收**，不等于真实商户/公网部署已验收；生产入口、备份恢复、真实支付另按[分层验收手册](docs/ACCEPTANCE_GUIDE.md)执行。更深入的 Conda 维护、备份恢复见[原 Conda 指南](docs/WINDOWS_CONDA.md)，不用在第一次操作时来回翻阅。

## 第三批补充：文件快照与旧库

第8节创建测试ZIP必须成功后再下单。新下单会在storage下生成`.snapshots`，不要删除或覆盖它；它保存已购买版本，不是临时缓存。现在换原商品文件，旧订单仍下载原版本；完整备份必须包含这些目录。文件上限512MiB，需要支持硬链接的本地卷（Windows实机验证尚待你按步骤执行，不能把Linux结果当NTFS验收）。

已有本项目旧库不是新库练习：先停止服务、备份并核对恢复，再按数据库指南运行migrate到当前0011（包含原0010合同升级）。历史订单会要求管理员核实原交易和原文件后绑定，不会自动用今天的商品替代。人工收款参数已收紧，详细JSON、只读ledger和旧单核准见[第三批说明](review/RELEASE_BLOCKERS_PHASE3.md)。先继续使用本指南的隔离mock验收，别用真实付款测试这些步骤。

## 第四批补充：打开订单与收款管理页

继续使用前文 VS Code 集成 **CMD + Conda + 系统 Node**，不切换到PowerShell或venv。后端仍是Python，Node只用于构建/测试。不要把pytest连到业务库。

1. 在已激活Conda、位于项目根目录的CMD执行：

```cmd
python -m pytest -q tests/test_payment_queries.py tests/test_payments_admin.py tests/test_payments_frontend.py
```

这一步用合成密钥，不需要真实微信账号，不扣款；Node缺失时先修前文系统Node路径，不能把跳过当完成。

2. 按前文启动隔离开发服务，在管理员浏览器打开 `http://127.0.0.1:8000/admin/payments`，或点顶栏“订单管理（管理员）”。未登录先登录自己创建的管理员。
3. 搜索测试订单并点开，核对客户ID、金额、冻结文件、收款条和过程记录。默认mock的单不会提供人工/微信确认按钮，这是正确渠道隔离。
4. 用另一个普通用户浏览器打开同一路径，应只显示无管理权限提示，不泄漏数据；同一浏览器切账号后，旧详情和依据应清空。检查窄屏、分页、刷新和错误提示。
5. manual/历史绑定的隔离试验、真实微信查单步骤见[工作台手册](docs/PAYMENTS_ADMIN_GUIDE.md)。没有真实凭据不测真实收款，不拿假流水去确认真实订单；本批没有退款按钮或自动撤销下载权。

本机浏览器和真实商户步骤未由沙箱代办验收；请实际操作后在自己的验收记录标记结果。管理页不要求生产开启/docs。

## 第五批补充：复核工作进度，不测试真实退款

继续用VS Code集成CMD、Conda和系统Node。在隔离验收环境执行：

```cmd
python -m pytest -q tests/test_payment_review.py tests/test_payments_frontend.py
```

浏览器管理员打开订单管理，选测试订单、输入完整单号和3–160字说明，再记录跟进、完成本轮、重新跟进。每次确认资金和已购下载权没有改变。切换范围后应从新范围开始；空列表若仍有下一页请继续查。不同管理员旧页面竞争、断网后原请求重试和换账号清屏需真实浏览器留证；这些步骤不表示已验收微信退款。更多边界见工作台手册第五批补充。

## 第六批增补：在独立测试环境检查退款与下载

仍使用VS Code集成CMD、Conda Python和系统Node，不改用venv。先按前面的独立测试数据库步骤，确认不是业务库。运行 `python -m pytest tests/test_refunds.py tests/test_download.py tests/test_payments_frontend.py -q` 与 `npm run build`；Windows若pgserver不可用会跳过其专属案例，应另用真实PostgreSQL验收，而不是把skip记为通过。本沙箱测试结果不代表你的Windows通过。

页面人工流程：在隔离的manual演示环境创建固定文件订单，管理员按演示证据确认收款，客户领链接并保留；用明确虚构的测试退款记录/带时区时间确认全额退款。刷新后，原收款仍在、客户显示已退款、旧链接与重领均403，另一已付单正常。不要在真实订单上填写演示凭证；这一步没有实际银行转账，也不是真实退款验收。微信入口须真实原商户配置/原付款凭证及已办理退款的商户退款号，本轮未替你执行真实操作。

升级存量库先备份并停写，使用维护CLI迁移到0011，不重跑init。旧链接需要未退款客户从订单历史重领；不要回退到不识别退款表的旧下载服务。完整规则见[管理员操作手册](docs/PAYMENTS_ADMIN_GUIDE.md)。
