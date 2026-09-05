# 在 Windows 上本地跑起来（cmd 版）

> 本文所有命令都是 **cmd.exe** 可直接粘贴执行的，一条一行，不含 `&&` 链、
> 不含 shell 通配符展开（cmd 不展开 `*`）、不含 `ls`/`grep`/`tail` 这类 POSIX 命令。
>
> 文中带 ✅ 的结论都是在真实环境里跑出来验证过的，不是推测。

---

## 0. 先装两样东西

### Python **3.11**（不要用 3.12 以上）

去 https://www.python.org/downloads/release/python-3119/ 下载
`Windows installer (64-bit)`，安装时**务必勾选 "Add python.exe to PATH"**。

**为什么必须是 3.11**（✅ 实测）：`requirements.txt` 里的 `pgserver==0.1.4`
在 PyPI 上**根本没有 Python 3.13 的发行版**：

```cmd
pip download pgserver==0.1.4 --python-version 3.13
ERROR: Could not find a version that satisfies the requirement pgserver==0.1.4
```

而 3.11 有现成的 Windows 包（✅ 已下载验证）：
`pgserver-0.1.4-cp311-cp311-win_amd64.whl`

装完确认：

```cmd
python --version
```

看到 `Python 3.11.x` 才行。

### PostgreSQL 16

去 https://www.postgresql.org/download/windows/ 下载安装，
安装向导里设置的 **postgres 超级用户密码要记住**，端口保持默认 **5432**。

#### 装完需要先「把服务拉起来」吗？

**通常不用** —— Windows 版安装包会把 PostgreSQL 注册成一个 **Windows 服务**，
默认设为「自动」启动，装完就已经在跑了。但**建议确认一下**，因为
`db_init.py` 是通过 TCP 连 `localhost:5432` 的，服务没起就会连接失败。

查服务在不在跑（服务名里的 `16` 对应大版本号，装的是别的版本就改成对应数字）：

```cmd
sc query postgresql-x64-16
```

> 看到 `STATE : 4  RUNNING` 就是在跑。
> 如果提示「指定的服务未安装」，说明服务名不对，用下面这条列出所有含 postgres 的服务：
>
> ```cmd
> sc query type= service state= all | findstr /i postgres
> ```

没在跑就手动起（**需要以管理员身份打开 cmd**）：

```cmd
net start postgresql-x64-16
```

> 也可以按 `Win+R` 输入 `services.msc`，找到 `postgresql-x64-16` 右键「启动」，
> 顺手把「启动类型」确认成「自动」，这样以后开机就自己起来了。

想直接验证能不能连上（`psql` 在安装目录的 `bin` 下，装的时候勾了加 PATH 才能直接敲）：

```cmd
psql -U postgres -h localhost -c "select version();"
```

> 会提示输入密码，就是安装向导里设的那个。能打印出版本号就说明库是通的。
> **这一步不是必须的** —— 跳过它直接做第 4 步也行，`db_init.py` 连不上会报很清楚的错。

---

## 1. 拿代码

已经克隆过的话，进目录拉最新：

```cmd
cd /d 你的路径\codemax_platform
```

```cmd
git checkout arena/01a0599b-codemax-platform
```

```cmd
git pull origin arena/01a0599b-codemax-platform
```

没克隆过的话：

```cmd
git clone https://github.com/cccjvav/codemax_platform.git
```

```cmd
cd /d codemax_platform
```

```cmd
git checkout arena/01a0599b-codemax-platform
```

---

## 2. 建虚拟环境、装依赖

下面 **A（venv）与 B（conda）二选一**，两者完全等价，后面的步骤一模一样。

### 方案 A：venv（Python 自带，无需额外安装）

```cmd
python -m venv .venv
```

```cmd
.venv\Scripts\activate
```

激活成功后命令行开头会出现 `(.venv)`。

```cmd
python -m pip install --upgrade pip
```

```cmd
pip install -r requirements.txt
```

### 方案 B：conda

**关键：Python 版本必须是 3.11**（原因见 §0：`pgserver` 没有 3.13 的 wheel）。
建环境时直接指定版本，别用 conda 的默认版本：

```cmd
conda create -n codemax python=3.11 -y
```

```cmd
conda activate codemax
```

激活成功后命令行开头会出现 `(codemax)`。

```cmd
python -m pip install --upgrade pip
```

```cmd
pip install -r requirements.txt
```

> **装依赖这一步用 `pip`，不要用 `conda install`。**
> `requirements.txt` 里的版本是逐个验证过的（`fastapi==0.104.1`、`sqlalchemy==2.0.23` 等），
> conda 渠道里的版本与这些钉子对不上，混装容易出现解析冲突。
> **conda 只负责管 Python 解释器本身，包交给 pip** —— 这是很常见的搭配，没有冲突。

> **不要用 `conda install postgresql`。** 你本机已经装了 PostgreSQL 服务，
> conda 再装一个会变成两套互不相干的库，纯属自找麻烦（详见 §4.1）。

两种方案后续完全一致：`activate` 之后，本文剩下所有 `python ...` / `pip ...`
命令都照抄即可（本文用 `python` 而不是 `.venv\Scripts\python.exe`，
conda 环境下正好就是这样）。

**这一步在 Windows 上能装成功**（✅ 逐个依赖验证过）：
21 个依赖都有 `win_amd64 / cp311` 的现成 wheel；只有 `jieba` 是 sdist
（`jieba-0.42.1.tar.gz`），但它是**纯 Python**，pip 会在本地打包，
**不需要装 C 编译器**。

---

## 3. 配 `.env`

```cmd
copy .env.example .env
```

```cmd
notepad .env
```

**最少只要改这几行**（其余留空也能启动）：

```dotenv
DB_HOST=localhost
DB_PORT=5432
DB_NAME=codemax_db
DB_USER=postgres
DB_PASSWORD=你装 PostgreSQL 时设的密码
```

想让支付流程能点通（本地演示用，**不接真微信**）：

```dotenv
SHOP_PAY_MODE=mock
```

想让「自然语言转 UML」和智能客服的闲聊分支真能答（可选，不填就自动转人工）：

```dotenv
LLM_API_KEY=你的key
LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
LLM_MODEL=qwen-plus
```

改完存盘关掉记事本。

---

## 4. 建库 + 建表（**这一步最容易踩坑**）

⚠️ **必须先 `cd` 进 `database init` 目录再跑脚本。**

原因（✅ 已读源码确认）：`db_init.py` 里读配置写的是相对路径

```python
load_dotenv(dotenv_path="../.env")
```

在项目根目录直接跑 `python "database init/db_init.py"` 会**找不到 `.env`**，
`DB_PASSWORD` 退化成空字符串，然后连接数据库失败。

目录名带空格，所以路径要加引号：

```cmd
cd "database init"
```

```cmd
python db_init.py
```

看到这两行就成功了：

```text
[1/2] 数据库 codemax_db 创建成功
[2/2] 已在 codemax_db 中执行 ...\full_init.sql，建表完成
```

（如果库已经建过，第一行会是 `[1/2] 数据库 codemax_db 已存在，跳过创建`，也正常。）

回项目根目录：

```cmd
cd ..
```

> ⚠️ **本项目不会自动建表。** 应用启动时不执行 `create_all`，
> 跳过这一步的话，任何查库的接口都会报 `relation ... does not exist`。

---

### 4.1 用 conda 的话，本机装的 PostgreSQL 会有影响吗？

**不会。两者完全不相干，这正是最常规的搭配。**

原因说清楚，以后遇到类似情况自己就能判断：

```text
┌─────────────────────────┐         TCP localhost:5432        ┌──────────────────────┐
│  Python（conda 环境）    │  ───────────────────────────────► │  PostgreSQL 服务      │
│  psycopg2-binary        │        普通的网络连接              │  （本机独立进程）     │
│  asyncpg                │  ◄─────────────────────────────── │  Windows 服务托管     │
└─────────────────────────┘                                   └──────────────────────┘
```

Python 只是一个**通过网络连接的客户端**，跟它装在 conda 里、venv 里、还是系统目录里，
**一点关系都没有**。PostgreSQL 是另一个完全独立的进程，由 Windows 服务托管。
换 Python 不会动到数据库，换数据库也不会动到 Python。

**唯一需要保证的是**：数据库驱动装在**你正在用的那个 conda 环境里**。
而 `pip install -r requirements.txt` 已经装好了 —— 就是这两个：

| 包 | 用在哪 |
| --- | --- |
| `psycopg2-binary==2.9.9` | 建库脚本 `db_init.py`（同步） |
| `asyncpg==0.29.0` | 应用运行时（异步） |

> **为什么钉的是 `psycopg2-binary` 而不是 `psycopg2`**：
> `-binary` 的 wheel **自带一份 libpq**（实测包内有 `psycopg2_binary.libs/libpq-*.so`／
> Windows 上是对应的 `.dll`）。所以它**不需要**你本机 PostgreSQL 的客户端库，
> 也**不需要** `pg_config` 在 PATH 上 —— 装的时候不编译任何东西。
> 换成非 binary 的 `psycopg2` 就会去调用本机的 `pg_config` 现场编译，
> 那才会跟「conda 环境」和「本机 PG」的 PATH 纠缠起来。

### 4.2 用 conda 时唯一要留意的两件小事

**① 别用 `conda install postgresql`。** 你本机已经有 PostgreSQL 服务了，
conda 再装一份会得到两套互不相干的库和数据目录，纯属自找麻烦。
conda 在这里只管一件事：**提供一个 3.11 的 Python 解释器**。

**② 手敲 `psql` 时可能敲到 conda 里的那个。** 如果某个 conda 环境里装过带 `psql` 的包，
激活后 PATH 优先找到它，版本可能与本机服务对不上。**这不影响本项目运行**
（项目走的是 `psycopg2-binary` / `asyncpg`，不经过命令行的 `psql`），
只在你手动用 `psql` 排查时才需要注意。确认用的是哪一个：

```cmd
where psql
```

> 列出多行就说明 PATH 里有好几个，第一行是实际会执行的那个。
> 想强制用本机 PostgreSQL 的，写全路径，例如
> `"C:\Program Files\PostgreSQL\16\bin\psql.exe" -U postgres -h localhost`

---

## 5. 启动服务

```cmd
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

看到这行就是起来了：

```text
INFO:     Uvicorn running on http://0.0.0.0:8000 (Press CTRL+C to quit)
```

**这个窗口不能关**，关了服务就停了。停止服务按 `Ctrl + C`。

---

## 6. 浏览器打开这些地址

✅ 以下 7 个地址都实测返回 **200**：

| 地址 | 是什么 |
|---|---|
| http://127.0.0.1:8000/ | 首页（工具矩阵） |
| http://127.0.0.1:8000/tools/er | SQL DDL 转 ER 图 |
| http://127.0.0.1:8000/tools/mermaid | 自然语言生成 UML 类图（**要配 LLM key**） |
| http://127.0.0.1:8000/tools/drawio | Drawio 在线流程图 |
| http://127.0.0.1:8000/docs | **Swagger 交互文档，测接口全靠它** |
| http://127.0.0.1:8000/sitemap.xml | 站点地图 |
| http://127.0.0.1:8000/robots.txt | 爬虫协议 |

---

## 7. 测智能客服（本次新做的 S4-02）

**别用 cmd 的 `curl`** —— cmd 里 JSON 的引号转义非常难写对。
用 `/docs` 页面点，最省事：

1. 打开 http://127.0.0.1:8000/docs
2. 找到 **智能客服 → POST /support/ask**
3. 点 **Try it out**
4. 把请求体改成下面任意一个，点 **Execute**

### 四种提问会走四条不同的路

**① 高频问题 → FAQ 秒回（不调大模型）**

```json
{"text": "毕业设计服务怎么收费"}
```

预期：`"intent": "faq"`、`"source": "faq"`、`"escalated": false`，
`answer` 是真实答案，`confidence` 约 0.537。

**② 闲聊 → 走轻量 LLM**

```json
{"text": "你好"}
```

没配 `LLM_API_KEY` 时：`"intent": "chitchat"`、`"source": "human"`、
`"escalated": true`，`reason` 里写「未配置 …」—— 这是**故意的兜底**，不是 bug。

**③ 专业问题 → RAG（要库里先有文章）**

```json
{"text": "python 部署 nginx 报错怎么排查"}
```

库里 `sys_article` 没数据时会返回
`"reason": "知识库不可用（sys_article 不存在或数据库异常），RAG 无法作答"`
并且 `"escalated": true`。想看完整的 RAG 效果，得先跑 S4-01 的爬虫往库里灌文章。

**④ 明确要人工 → 直接转人工**

```json
{"text": "我要人工"}
```

预期：`"escalated": true`，`reason` 是「用户明确要求人工」。

### ✅ 真机冒烟结果（uvicorn + 真 PostgreSQL 16）

| 提问 | HTTP | intent | source | escalated |
|---|---|---|---|---|
| 毕业设计服务怎么收费 | 200 | faq | faq | false |
| 你好（未配 key） | 200 | chitchat | human | true |
| python 部署 nginx 报错怎么排查 | 200 | professional | human | true |
| 我要人工 | 200 | chitchat | human | true |
| asdfghjkl 嗯嗯 | 200 | chitchat | human | true |

**日志里 500 的条数 = 0。** 四种情况全部退到「转人工」而不是抛异常给用户，
这正是 S4-02-4 兜底机制要的效果。

---

## 8. 跑测试

```cmd
python -m pytest -q
```

✅ 预期：**532 passed, 4 skipped**（SQLite 后端，约 3 分半）。

静态检查：

```cmd
python -m ruff check .
```

✅ 预期：`All checks passed!`

---

## 9. 常见报错对照

| 报错 | 原因 | 怎么办 |
|---|---|---|
| `'python' 不是内部或外部命令` | 装 Python 时没勾 Add to PATH | 重装并勾选，或改用完整路径 |
| `pip install` 报 pgserver 找不到版本 | Python 是 3.13 | 换 **3.11** |
| `password authentication failed` | `.env` 里 `DB_PASSWORD` 不对 | 改成装 PG 时设的密码 |
| `connection refused` / `could not connect` | PostgreSQL 服务没启动 | Win+R → `services.msc` → 启动 `postgresql-x64-16` |
| `relation "sys_user" does not exist` | 跳过了第 4 步 | `cd "database init"` 后跑 `python db_init.py` |
| `db_init.py` 报 `FATAL: password authentication failed` 但密码明明对 | 在项目根目录跑的，没读到 `.env` | **先 `cd "database init"`** |
| `/tools/mermaid` 返回 502 | 没配 `LLM_API_KEY` | 配 key，或忽略（不影响其他功能） |
| 端口 8000 被占用 | 上次没关干净 | 换个端口：`--port 8001` |
| `conda activate` 报 `CommandNotFoundError` | conda 没给 cmd 做过初始化 | 跑 `conda init cmd.exe`，**关掉 cmd 重开**，再 activate |
| conda 环境里 `pip install -r requirements.txt` 报找不到某个版本 | 之前用 `conda install` 装过同名包，版本被钉住了 | `conda create` 一个**干净**环境重来，装依赖只用 `pip`（见 §2 方案 B） |
| `psql` 敲出来的版本跟本机服务不一致 | conda 环境里也有个 `psql`，PATH 优先找到它 | 不影响项目运行；要用本机的就写全路径（见 §4.2） |

---

## 10. 一句话速查

从零开始，一共这几条（假设已装好 Python 3.11 和 PostgreSQL 服务）。

**用 venv：**

```cmd
python -m venv .venv
```
```cmd
.venv\Scripts\activate
```

**或改用 conda（只有这两条不同，其余照抄）：**

```cmd
conda create -n codemax python=3.11 -y
```
```cmd
conda activate codemax
```

**接下来两种方案完全一样：**

```cmd
pip install -r requirements.txt
```
```cmd
copy .env.example .env
```
```cmd
notepad .env
```
```cmd
cd "database init"
```
```cmd
python db_init.py
```
```cmd
cd ..
```
```cmd
python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

然后浏览器打开 http://127.0.0.1:8000/docs

> **`db_init.py` 报连接失败**，先确认 PostgreSQL 服务在跑（见 §0「装完需要先起服务吗」）：
>
> ```cmd
> sc query postgresql-x64-16
> ```
>
> 不是 `RUNNING` 就用管理员 cmd 执行 `net start postgresql-x64-16`。

---

## 11. 看文档站（**不需要数据库、不需要 `.env`**）

架构导读与代码级说明书有一个可读 + 可视化的静态站点版本。
**它跟上面第 0-10 步完全无关** —— 不需要装 PostgreSQL、不需要配 `.env`、
不需要装 `requirements.txt` 里那 25 个依赖，只要有 Python 和一个几 MB 的包。

```cmd
pip install mistune
```

```cmd
python scripts\build_docs_site.py
```

```cmd
start docs\site\index.html
```

打开后能看到：

- **21 份文档**逐份渲染，右侧有目录，正文里的 `app/routers/diagrams.py:147` 这类引用可以点，
  点进去是**目标行已高亮**的源码页
- **模块依赖图**（40 个模块 / 85 条依赖，鼠标悬停高亮关系）
- **路由地图**（38 条路由，标注鉴权与限流，可过滤）
- **符号索引**（250+ 个函数与类，点一下直达源码定义行）

完整说明与常见问题见 [docs/site/README.md](./site/README.md)。

> **搜索框在 `file://` 下可能被浏览器拦**（它用 `fetch` 读索引）。
> 那时用浏览器自带的 `Ctrl+F` 即可，正文、目录、图表、跳转都不受影响。
> 想让搜索框也能用，就起个本地服务：
>
> ```cmd
> python -m http.server 8000 --directory docs\site
> ```
>
> 然后浏览器打开 http://localhost:8000

> **改了文档后页面不会自动变** —— 站点是预渲染的，重跑一次 `python scripts\build_docs_site.py` 即可（约 1 秒）。
