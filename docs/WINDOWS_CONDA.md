# Windows + conda：运行、维护与测试

本指南适用于你的 conda 环境，不要求创建 `.venv`。使用 **Anaconda Prompt / Miniconda Prompt（cmd.exe）**，不是 PowerShell；每行单独执行。示例目录 `C:\work\codemax_platform` 请改为你的实际路径。

conda 管理 Python 解释器和环境，项目依赖仍以 `requirements.txt` 与 `package-lock.json` 为准；不另维护一份容易漂移的 conda 包版本清单。本文命令已按仓库接口核对，但没有在你的 Windows/conda 环境实际执行，不能据此标记本机验收通过。

## 1. 环境准备

需要 conda、Git、Python 3.11 环境、PostgreSQL 16，以及用于构建和 Node 测试的 Node.js。Vite 锁文件要求 Node `^20.19.0 || >=22.12.0`；建议使用受支持的 Node 22（至少 22.12）或 24。不要为本项目更改 base 环境，也不要同时激活 conda 和 `.venv`。

尚未安装时使用官方入口：[conda Windows 安装](https://docs.conda.io/projects/conda/en/stable/user-guide/install/windows.html)、[PostgreSQL Windows 安装](https://www.postgresql.org/download/windows/)、[Node.js](https://nodejs.org/en/download)、[Git for Windows](https://git-scm.com/downloads/win)。已有 conda 不必再装一份系统 Python。PostgreSQL 安装时保留 Server 和命令行工具、记住端口及维护账号密码；Node/Git 安装后重新打开 Prompt，再核对 PATH。

```cmd
conda --version
conda env list
```

如果已有项目环境，先激活并检查，不要重复创建或删除它：

```cmd
conda activate codemax
set PYTHONUTF8=1
python --version
python -c "import sys; print(sys.executable)"
python -m pip --version
where python
where node
node --version
npm --version
```

`set PYTHONUTF8=1` 对当前终端的后续 Python 进程启用 UTF-8 默认编码，避免 Windows 默认代码页影响中文文件/测试；每个新终端都要设置，IDE 运行环境也要一致。这不是更改系统区域设置。

`codemax` 是示例环境名。解释器及 pip 路径应指向你选择的 conda 环境；若显示 `.venv`、base 或系统 Python，先修正激活状态。环境应为 Python 3.11；不要直接把有其他项目的环境降级，另建独立环境更安全。

尚无环境时才执行：

```cmd
conda create -n codemax python=3.11 pip -y
conda activate codemax
set PYTHONUTF8=1
```

若普通 cmd 不认识 conda，先用安装器提供的 Prompt；不要把任意网上提供的目录硬塞进 PATH。若要给普通 cmd 初始化，可在 conda Prompt 运行 `conda init cmd.exe`，关闭后重新打开 cmd。这会修改 shell 初始化配置，不是安装项目依赖。

## 2. 获取代码与安装依赖

已有仓库先查看状态；有本地修改先保留，不使用 reset/clean 强制覆盖：

```cmd
cd /d C:\work\codemax_platform
git status --short
git branch --show-current
```

本轮交付在 `arena/01a08bf5-codemax-platform`。确认当前分支是它且可以快进后再拉取：

```cmd
git pull --ff-only origin arena/01a08bf5-codemax-platform
```

若当前不是这个分支，不在有修改的目录强行切换；可以在新目录克隆本轮分支：

```cmd
git clone -c core.autocrlf=false --branch arena/01a08bf5-codemax-platform --single-branch https://github.com/cccjvav/codemax_platform.git C:\work\codemax_platform
```

仓库指纹按实际字节计算，当前 `.gitattributes` 未强制工作树 LF。上面 `clone -c core.autocrlf=false` 只设置新仓库，防止 Windows 自动转 CRLF 造成大量“指纹过期”；编辑器也选 UTF-8 / LF。已有目录发生这个问题时保留修改，在新目录按该命令重克隆并对照，不用强制检出或 `--write` 批量掩盖换行差异。

克隆命令只用于尚不存在的目标目录；仓库认证使用你已配置的 GitHub 连接，不把密码或令牌放入命令。

激活 conda 后，在项目根目录执行：

```cmd
python -m pip install -r requirements.txt
python -m pip check
npm ci
npm run build
```

始终用 `python -m pip`，避免调用到另一个环境的 pip。不要再用 `.venv\Scripts\python.exe`，也不要用可能绕过 conda 的 `py` 启动器。Conda 环境中通过 pip 安装是本指南的选择；不要再用 conda 安装同一批 FastAPI/SQLAlchemy 等包来反复覆盖它们。

`npm ci` 使用锁文件并重建 node_modules；正常运行后端不需要 Node 服务，但前端构建和完整测试需要 node 命令可用。不要随意运行 `npm audit fix --force` 或全环境 `conda update --all`。

## 3. 本地配置与数据库

### 3.1 不覆盖已有 .env

```cmd
if not exist .env copy .env.example .env
notepad .env
```

本地应用建议配置：

| 设置 | 本地用途 |
| --- | --- |
| `ENV=development` | 本地 HTTP 与开发 API 文档；不是生产配置 |
| `DB_HOST=127.0.0.1`、`DB_PORT=5432` | Windows PostgreSQL 服务地址，不是 Compose 的 db 主机名 |
| `DB_NAME=codemax_dev` | 专用开发库，不能是现有业务库或自动化测试库 |
| `DB_USER`、`DB_PASSWORD` | 本地数据库账号及真实密码，只保存在本机 |
| `DATABASE_URL=` | 通常留空，让应用按 DB 字段构造连接串；已有该项时会优先于 DB 字段 |
| `SECRET_KEY` | 本地专用随机密钥；改动会影响已有 token/下载签名，不用生产密钥 |
| `SHOP_PAY_MODE=mock` | 仅本地模拟付款，不会真实收钱，生产禁止 |
| `SITE_BASE_URL=http://127.0.0.1:8000` | 本地规范链接，不是上线域名 |
| `TRUST_PROXY_HEADERS=false` | 直接访问本地服务时不信任代理头 |
| `STORAGE_BACKEND=local`、`STORAGE_LOCAL_ROOT=storage` | 本地商品目录；必须自己准备测试文件 |
| `LLM_API_KEY` 等 | 普通离线测试不需要；真实模型验收另行配置，见验收手册 |

新开发环境可在本机生成 SECRET_KEY，输出仅复制进私密 `.env`，不截图/上传：

```cmd
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

环境变量优先于 `.env`。如果改文件不生效，检查是否曾在 shell 或 conda 环境中持久设置同名变量；不要把包含密钥的环境列表截图发出来。

PostgreSQL 是独立服务，`conda activate` 不会启动它。可在 Windows 服务管理器检查，或执行：

```cmd
sc query postgresql-x64-16
psql -h 127.0.0.1 -U postgres -d postgres -c "select version();"
```

服务名可能不同；psql 不在 PATH 时使用 PostgreSQL 安装目录中的 `bin\psql.exe`。数据库密码在交互提示输入，不写进聊天。Docker Compose 默认未发布数据库端口，不能假定 conda 进程能通过 localhost 直接访问其中的 db。

### 3.2 只有新开发库才初始化

**`full_init.sql` 会删表。已有数据库不能为了“修环境”重跑初始化。** `db_init.py` 使用 DB 字段读取 `../.env`，不使用 DATABASE_URL；务必从下面的目录运行：

```cmd
cd "database init"
python db_init.py
cd ..
```

看到建库/建表成功还应确认操作的是 `codemax_dev`。只有具备建库权限的本地维护账号才能创建新库。存量升级先备份与停写，再按已具备的结构执行缺失迁移；已有 0006 的库只补 0007/0008，详见 [数据库指南](../database%20init/README.md)。Conda 重建不会替你升级数据库。

## 4. 每天运行与停止

每个新终端都需要激活环境并进入根目录：

```cmd
conda activate codemax
set PYTHONUTF8=1
cd /d C:\work\codemax_platform
python -m uvicorn main:app --reload --host 127.0.0.1 --port 8000 --no-proxy-headers
```

浏览器访问 `http://127.0.0.1:8000/`、`/docs`、`/support/center`。后端保持这个窗口运行；另开已激活的 Prompt 执行其他命令。`Ctrl+C` 停服务，`conda deactivate` 退出环境；退出 conda 本身不是停止已在别处运行的服务。

只改文档不必重启应用；改 `.env` 建议停止后重新启动。修改前端后执行 `npm run build` 再刷新浏览器。不要为了本机访问改为公开监听；局域网/公网访问另行配置防火墙、入口代理和 HTTPS。

IDE/编辑器也要选择上述 `sys.executable` 对应解释器，否则终端能跑而编辑器报缺包。未使用 shell 激活时，可从根目录运行 `conda run -n codemax --no-capture-output python -m uvicorn main:app --host 127.0.0.1 --port 8000 --no-proxy-headers`。

## 5. 维护与恢复

- **拉取新代码**：先保留未提交修改，再快进拉取；requirements 改动后重新 pip install，锁文件改动后 npm ci，前端改动后 npm run build。
- **改 Python 版本或依赖冲突**：优先新建 `codemax_recheck` 环境重装验证，不原地破坏可工作的环境。`python -m pip check` 检查依赖一致性，但不等于测试通过。
- **正常维护**：不要把 `pip freeze` 覆盖 requirements；它会把 conda/平台相关与间接依赖混入项目清单。依赖升级需单独评审、测试和扫描。
- **重建环境**：只重建解释器/依赖，`.env`、数据库、storage 都需单独备份。不要删除正在用的环境或运行 `docker compose down -v` 当作普通重启。
- **验收留档**：记录 commit、Python/Node/conda 版本、测试结果。可将环境快照放仓库外，例如 `conda env export --no-builds > C:\work\codemax-env-local.yml`；导出可能含本地路径、渠道或环境变量，检查脱敏后再分享，不能作为跨平台锁文件。

### 5.1 备份与恢复演练（本地开发库示例）

先在仓库外创建 `C:\work\codemax-backups`，并为每次备份选择**未使用的文件名**。以下路径中的 before_change 是示例，不覆盖旧备份。pg_dump/createdb/pg_restore 与 psql 一样来自 PostgreSQL 的 bin 目录。

```cmd
pg_dump -h 127.0.0.1 -U postgres -Fc -d codemax_dev -f C:\work\codemax-backups\codemax_dev_before_change.dump
```

应退出 0 且生成非空备份文件。另将本地 `.env`、storage 目录备份到该私密目录；数据库备份不包含磁盘上的商品文件，conda 环境导出也不包含它们。备份包含敏感数据，不提交 Git、不公开上传。

要验证可恢复，先确认演练库名不存在；下条 createdb 失败就停止，不能继续往已有库灌数据：

```cmd
createdb -h 127.0.0.1 -U postgres codemax_restore_check
pg_restore --exit-on-error --no-owner -h 127.0.0.1 -U postgres -d codemax_restore_check C:\work\codemax-backups\codemax_dev_before_change.dump
psql -h 127.0.0.1 -U postgres -d codemax_restore_check -c "select count(*) from sys_user; select count(*) from sys_order;"
```

核对数量与备份时一致，再在隔离配置下做读取检查。这里只示范恢复到新库，不覆盖运行库；生产备份/迁移/回滚还需按部署指南安排停写窗口和权限。

## 6. 自动化测试：与浏览器演示分开

完整测试会创建/删除测试表，不能指向开发库或生产库。部分测试还要求默认配置；应用目录的 mock 模式、真实模型 key 或改过的语义阈值可能改变测试前提。

**发布验收建议使用同一 conda 环境、一个没有 .env 的独立干净代码目录**，不复制商品文件和业务数据库。首次创建该目录时：

```cmd
git clone -c core.autocrlf=false --branch arena/01a08bf5-codemax-platform --single-branch https://github.com/cccjvav/codemax_platform.git C:\work\codemax_acceptance
cd /d C:\work\codemax_acceptance
conda activate codemax
set PYTHONUTF8=1
git rev-parse HEAD
python -m pip install -r requirements.txt
npm ci
```

检查验收 SHA 与要验收的提交相同；未提交的开发修改不会自动出现在此目录。日常定向测试应在实际修改目录跑，若失败来自配置前提，不删断言，先用干净副本核对。新的测试终端不要继承生产配置或 conda 持久业务变量。

### 6.1 默认 SQLite 与构建

在**无 .env 的验收目录**清除可能残留的配置选择，然后逐项执行：

```cmd
set TEST_DATABASE_URL=
set DATABASE_URL=
set ENV=
set SHOP_PAY_MODE=
set LLM_API_KEY=
set LLM_SEMANTIC_THRESHOLD=
python -m pip check
python -m ruff check .
python -m pytest -q -rs
```

每项结束立即检查输出；可用 `echo %ERRORLEVEL%` 查看退出码，非 0 就停止排错，不被后续成功命令覆盖。

```cmd
npm run build
git diff --exit-code -- app/static/js
npm audit --audit-level=high
python scripts/check_docs_contract.py
python scripts/build_docs_site.py
```

提交后还需查看 [GitHub Actions](https://github.com/cccjvav/codemax_platform/actions) 中**同一 SHA** 的 Ruff、文档、前端产物、SQLite、PostgreSQL 和 pip-audit 六个 job。`pip check` 只查依赖关系，不扫描漏洞；Python 漏洞扫描由 pip-audit job 执行，已有已知风险例外及原因见 TD-213，不能随意添加豁免。CI 是 Linux 环境，不是本机 conda 验收证明。

构建产物应与提交版本一致。Mermaid 大分块警告不等于失败；依赖漏洞结果应按实际扫描记录处理。门禁失败先读源码/说明，不用 `--write` 自动“修绿”；它只用于已经完成说明复核后的有意更新。

### 6.2 真实 PostgreSQL 测试

SQLite 不能验收真实 PostgreSQL 的并发和迁移。可用本机 PG，也可用隔离的测试服务器或 CI，不要求 Windows 专有环境。

在本机 PG 用维护账号操作，仅在**尚未创建**测试角色/库时进行创建。先在 cmd 打开 psql：

```cmd
psql -h 127.0.0.1 -U postgres -d postgres
```

看到 psql 提示符后，以下命令只在 psql 内执行，不在 cmd 直接执行：

```sql
CREATE ROLE codemax_test LOGIN;
\password codemax_test
CREATE DATABASE codemax_test OWNER codemax_test;
\q
```

`\password` 会交互设置测试专用密码。角色不需要超级用户权限；它拥有专用测试库即可。如果角色/库已存在先核对用途，不删除或重建有数据的库。测试库不要运行 full_init，测试 fixture 会创建结构。

回到**无 .env 的验收目录**，下面一行只在当前 Python 子进程设置连接串，交互读取密码并自动 URL 编码，不写入 shell 历史或 `.env`：

```cmd
python -c "import os, getpass, pytest; from sqlalchemy.engine import URL; os.environ['TEST_DATABASE_URL']=URL.create('postgresql+asyncpg', username='codemax_test', password=getpass.getpass('Test DB password: '), host='127.0.0.1', port=5432, database='codemax_test').render_as_string(hide_password=False); raise SystemExit(pytest.main(['-q', '-rs']))"
```

调整地址/端口/库名时仍只指向专用测试库。失败时先核对服务、连接、权限和具体断言，不能增加 skip 冒充通过。

### 6.3 覆盖率与结果判断

```cmd
python -m coverage run -m pytest -q
python -m coverage report
```

此命令不继承上面子进程的 PG 设置，因此在清空 TEST_DATABASE_URL 后是 SQLite 口径。报告必须注明后端、版本和跳过原因，不沿用历史覆盖率。历史 `cb7f2ba` 的 744/749 通过数仅供比较，不是永久硬编码门槛；以当前收集数及结果为准。

自动化全绿只是代码层验收，最后还要按 [人工及外部依赖验收手册](ACCEPTANCE_GUIDE.md) 检查真实浏览器、Word 文件、外部编辑器及实际启用的商业能力。
