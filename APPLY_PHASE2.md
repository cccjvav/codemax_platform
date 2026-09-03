> ⚠️ **本文件是一次性传输文档，内容已过期，仅作历史留存。**
> 它是早期会话之间搬运补丁用的，描述的是**当时**的仓库状态（测试条数、待办顺序等都已失效）。
> **不要照它操作。** 现在的事实来源是：`AGENTS.md`（命令与规矩）、`HANDOVER.md`（现状与下一步）、
> `ROADMAP.md`（进度）、`TECH_DECISIONS.md`（每条取舍的编号与代价）。
> 合并 PR #3 后本文件会被删除。

# 阶段二补丁 · 使用说明（给下一个会话的助手）

> 配套文件：`PHASE2_TRANSFER.txt`（`git format-patch` 格式，可直接 `git am`）
> 基准：`main` = `3d1c4db`（PR #2 合并提交）。补丁已实测在其上干净应用，`32 passed`。

---

## 一、最快的路径：把文件传到 GitHub，新会话自己取

上一轮已验证这条路可行（`PHASE1_TRANSFER.txt` 就是这么送进来的）。

**你（用户）要做的：**
1. 打开 https://github.com/cccjvav/codemax_platform
2. `Add file` → `Upload files`，把 **`PHASE2_TRANSFER.txt`** 和 **`APPLY_PHASE2.md`** 传上去，提交到 `main`

**然后对新会话的助手说：**
> 从 `origin/main` 读取 `PHASE2_TRANSFER.txt` 和 `APPLY_PHASE2.md`，按说明应用补丁。

新会话执行（**注意顺序，前两步不能省**）：

```bash
# 1. Arena 沙箱是浅克隆，不补全会导致祖先关系误判
git fetch --unshallow origin
git fetch origin main

# 2. .venv 不跨会话保留，必须重建（约 30 秒）
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 3. 先确认基线是 16 passed 再动手
.venv/bin/python -m pytest -q

# 4. 从 main 取出补丁并应用
git show origin/main:PHASE2_TRANSFER.txt > /tmp/PHASE2_TRANSFER.txt
git config user.name "cccjvav"          # 缺身份会让 git am 报 empty ident name
git config user.email "66018270+cccjvav@users.noreply.github.com"
git am /tmp/PHASE2_TRANSFER.txt

# 5. 验证：应为 32 passed
.venv/bin/python -m pytest -q

# 6. 推送并开 PR（提交后立刻 push！）
git push origin <你的分支>
gh pr create --base main --head <你的分支> \
  --title "feat: 阶段二 S2-01-1 —— SQL DDL 解析与 ER 图数据接口" \
  --body "见 PHASE2_TRANSFER.txt 提交信息"
```

> `git am` 若报冲突（main 期间又变了）：改用 `git am --3way`；
> 仍失败则 `git am --abort` 后 `git apply --reject /tmp/PHASE2_TRANSFER.txt`，手工处理 `.rej`。

---

## 二、防误判检查

若助手说"已合并/无差别"，让它先跑：
```bash
git log origin/main..HEAD --oneline
git ls-files | grep '^app/tools/'
```
- 第一条为空 + 第二条为空 = 补丁未应用，执行上面的步骤
- 第一条 1 条提交 + 第二条有 `app/tools/sql_ddl.py` = 已就位，直接推送

另外：**浅克隆会让 `git merge-base --is-ancestor` 误判**。判断前先跑
`git rev-parse --is-shallow-repository`，若为 `true` 先 `git fetch --unshallow origin`。

---

## 三、补丁内容

| 文件 | 变更 |
| --- | --- |
| `app/tools/__init__.py` | 新增（工具子包） |
| `app/tools/sql_ddl.py` | 新增 206 行，DDL 解析器，**纯标准库无新依赖** |
| `app/routers/tools.py` | 新增 `POST /tools/er-diagram` |
| `app/schemas.py` | 新增 `ErDiagramIn`（ddl 长度 1–20000） |
| `tests/test_sql_ddl.py` | 新增 16 个用例（12 单元 + 4 接口） |
| `ROADMAP.md` | S2-01-1 拆成后端/前端子项，后端勾选 |

统计：`6 files changed, 392 insertions(+), 1 deletion(-)`

**接口**
```
POST /tools/er-diagram          # 无鉴权：引流工具，利于 SEO 收录与游客直用
Body: {"ddl": "CREATE TABLE ..."}
200:  {"tables":[{"name","columns":[{"name","type","primary_key","nullable","default","comment"}],"comment"}],
       "edges":[{"from_table","from_column","to_table","to_column"}]}
400:  未解析到任何 CREATE TABLE
422:  ddl 为空或超长
```

**实现要点（坑已处理，别改回去）**
- 先剥离 `--` 与 `/* */` 注释 —— 否则注释里的中文逗号（`-- 状态：1正常，0禁用`）会污染列切分
- 顶层逗号切分跳过括号与引号 —— `NUMERIC(10,2)` 不会被切成两列
- 括号配对用深度扫描而非正则 `.*?` —— 嵌套括号不会提前截断
- 兼容 MySQL（反引号 / inline `COMMENT` / 表级 `CONSTRAINT ... FOREIGN KEY`）与
  PostgreSQL（`SERIAL` / `COMMENT ON TABLE|COLUMN` / inline `REFERENCES`）
- 复合主键、`schema.表名` 前缀剥离、`DEFAULT '字面量'` 去引号
- **回归测试直接读项目自己的 `database init/full_init.sql`**，校验解析出 5 表 3 外键 ——
  这样 `app/models.py` 或 `full_init.sql` 一旦改动不同步，测试立刻报错

**实测输出**（输入 = 项目自身 `full_init.sql`）
```
表: ['sys_user', 'sys_order', 'sys_config', 'oauth_client', 'oauth_code']
外键: sys_order.user_id→sys_user.id
      oauth_code.user_id→sys_user.id
      oauth_code.client_id→oauth_client.id
```

---

## 四、验证记录（沙箱实测，非推断）

```
$ git clone <本地 origin/main>  →  起点 3d1c4db（PR #2 合并提交）
$ git am PHASE2_TRANSFER.txt    →  Applying: feat: 阶段二 S2-01-1 ...  退出码 0
$ .venv/bin/pip install -r requirements.txt   退出码 0
$ .venv/bin/python -m pytest -q
32 passed, 1 warning in 17.75s                退出码 0     ← 原 16 + 新增 16
```
唯一 warning 是 passlib 对 `crypt` 的 DeprecationWarning，与本补丁无关。

---

## 五、阶段二剩余任务（建议顺序）

按"后端可测优先、外部依赖靠后"：

1. **S2-01-1 前端** —— `app/static/er.html` 把 `tables/edges` 喂给 D3.js；`main.py` 挂 `StaticFiles`
2. **S2-01-2 LLM→Mermaid** —— LLM 客户端**必须可注入**，否则测试会真打网络：
   `async def generate_mermaid(text: str, llm: LLMClient = default_llm) -> str`
3. **S2-01-4 导出 Word** —— ⚠️ ROADMAP 写的 `Apache POI` 是 **Java** 库，本项目是 Python，
   应改用 `python-docx` 并同步修正 ROADMAP（阶段四的 `Jsoup` 同理 → `httpx` + `BeautifulSoup`）
4. **S2-01-3 Drawio** —— 前端为主；后端只需存取端点 + 新表 `sys_diagram`
   （**必须同时改 `app/models.py` 和 `database init/full_init.sql`**，这是 HANDOVER 点名的坑）；
   该端点要鉴权（用户私有资产），与 ER 工具的公开语义不同
5. **S2-02-1 SEO** —— ⚠️ 仓库目前是纯 JSON API，没有 HTML 页面，TDK/SSR 需先定前端方案。
   能立刻做的纯后端部分：`GET /sitemap.xml`、`GET /robots.txt`，工具清单集中成一个 `TOOLS` 常量
   同时驱动路由、sitemap 与前端导航
6. **S2-02-2 引流→变现** —— 工具响应里加可选 `upsell` 字段，跳转复用阶段一 SSO：
   `/oauth/authorize?client_id=shop&...`，不需要新机制

**提交粒度**：一个 PR 一件事，便于回滚。

---

## 六、铁律（上一个会话的事故教训）

**每次 `git commit` 之后必须立刻 `git push`。**

阶段一的 3 个提交就是因为只提交到沙箱本地、`git push` 没执行，会话一结束代码就没了，
最后靠人工传补丁才救回来。沙箱不跨会话共享，`.git` 与 `.venv` 都可能被重置 ——
本轮就实测到 `.git` 被回滚到 `e72e917`、`.venv` 整个消失。未推送的提交等于不存在。
