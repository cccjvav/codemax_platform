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

✅ 预期：**418 passed, 2 skipped**（SQLite 后端，约 3 分半）。

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

---

## 10. 一句话速查

从零开始，一共这几条（假设已装好 Python 3.11 和 PostgreSQL）：

```cmd
python -m venv .venv
```
```cmd
.venv\Scripts\activate
```
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

---

## 11. 看文档站（**不需要数据库、不需要 `.env`**）

架构导读与代码级说明书有一个可读 + 可视化的静态站点版本。
**它跟上面第 0-10 步完全无关** —— 不需要装 PostgreSQL、不需要配 `.env`、
不需要装 `requirements.txt` 里那 22 个依赖，只要有 Python 和一个几 MB 的包。

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

- **20 份文档**逐份渲染，右侧有目录，正文里的 `app/routers/diagrams.py:147` 这类引用可以点，
  点进去是**目标行已高亮**的源码页
- **模块依赖图**（40 个模块 / 85 条依赖，鼠标悬停高亮关系）
- **路由地图**（32 条路由，标注鉴权与限流，可过滤）
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
