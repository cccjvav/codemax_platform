# 复审报告（第 4 轮，超全面全目录版）

> ⚠️ **历史审查快照，仅供存档**
>
> 本文是某一轮的第三方复审记录，里面的统计数字、行号与结论都只代表**当时**的仓库状态，
> 之后已经变过。做全仓 `grep` 时很容易把它误当成当前事实来源 —— 当前真值请以
> `DOCUMENTATION_SUMMARY.md`、各目录 README 与实跑命令为准。
> 本文指出的问题是否已修复，见 `HANDOVER.md` §6 与 `TECH_DECISIONS.md`。


**审查目标**  
`arena/01a0599b-codemax-platform` 最新 fetched tip：`80a7b87d17684bc352ee5dc002246852dda4cf11`

**审查方式**  
只读分析；未修改目标分支代码。本文档是审查产物，写入当前 Arena 工作区，便于下载与归档。

**审查范围**  
按你的要求，本轮覆盖：

- 根目录文件
- `.claude/skills/`
- `.github/workflows/`
- `app/`
- `app/routers/`
- `app/static/`
- `app/templates/`
- `app/tools/`
- `database init/`
- `docs/`
- `docs/site/`
- `docs/site/data/`
- `scripts/`
- `tests/`

并且实际复跑了 lint、docs-site 构建、SQLite 全量测试、fresh PostgreSQL 全量测试。

---

## 一、总评结论

这次 `80a7b87` 的主改动集中在 **semantic FAQ 阈值配置化与标定测试补强**，整体方向是对的，代码质量也明显高于很多“只补文案、不补验证”的改动：

- 新增的 `LLM_SEMANTIC_THRESHOLD` 配置化思路合理
- `semantic_threshold()` 改为调用时读取配置，便于标定与测试 monkeypatch
- `calibrate_threshold()` 抽成纯函数，利于在无 key 环境下验证算法本体
- `tests/test_faq_semantic.py` 的增强是有牙齿的，不是凑覆盖率
- 后端主链路在 SQLite 与 fresh PostgreSQL 下都通过了全量验证

但是，本轮仍确认出以下问题：

### 结论摘要

1. **存在 1 个真实前端逻辑 bug（高优先级）**  
   商城页“登录后自动补单”仍有异步竞态窗口，可能在用户未来某次重新登录时触发**幽灵下单**。

2. **存在 1 个配置层缺口（中优先级）**  
   新增配置 `LLM_SEMANTIC_THRESHOLD` 没有限定在 `[0,1]`，误配会静默破坏语义 FAQ 命中判定。

3. **存在 1 组系统性文档漂移问题（中优先级，覆盖多目录）**  
   这次代码与测试变更后，多个 README / 指南 / skill 文档 / 总览文件中的计数和结构说明仍明显落后于当前仓库真实状态。

### 最终判断

> **可正面评价这次 semantic FAQ 改动本身，但不能判定该 tip 已经“彻底收尾”。**  
> 若按“能否放心继续往前推”的标准看，我建议至少先修掉 **商城补单竞态**，并补上 **阈值范围校验**；文档则需要安排一次系统性同步。

---

## 二、本轮实际执行验证

审查对象目录：`/tmp/codemax_0599b_r5`

### 1. 静态检查

```bash
ruff check .
```

结果：**通过**

### 2. 文档站数据提取

```bash
python scripts/build_docs_site.py --data-only
```

结果：**通过**

实测输出：

- `77 modules`
- `204 edges`
- `39 routes`
- `268 symbols`
- `21 docs`
- `10066 doc lines`

### 3. 文档站整站构建

```bash
python scripts/build_docs_site.py
```

结果：**通过**

实测生成：

- 文档页：`21`
- 源码页：`99`
- 另有首页与 3 个可视化页

### 4. SQLite 全量测试

```bash
python -m pytest -q
```

结果：

- **541 passed, 4 skipped, 4 warnings**

### 5. fresh PostgreSQL 全量测试

使用新建本地 pgserver 集群与独立测试库后执行：

```bash
TEST_DATABASE_URL='postgresql+asyncpg://postgres@/codemax_test_r5?host=/tmp/pgdata0599b_r5' python -m pytest -q
```

结果：

- **543 passed, 2 skipped, 2 warnings**

### 6. 补充核验

- FastAPI 业务 method-path 路由：**39 条**
- 唯一业务 path：**34 个**
- `docs/site/data/routes.json` 统计：
  - `auth = 18`
  - `rate_limit = 8`
- `Settings.model_fields`：**41 项配置**

---

## 三、针对本次提交核心改动的判断

本次提交 message 概括为：

> `fix(S4-02-5): 语义阈值改为配置项，并修好从未被执行过的标定用例`

我重点复核了：

- `app/config.py`
- `app/tools/faq.py`
- `app/tools/support.py`
- `tests/test_faq_semantic.py`
- `.env.example`
- `TECH_DECISIONS.md`

### 正面结论

#### 1. 阈值配置化方向正确

`LLM_EMBED_MODEL` 既然本来就是配置项，那么 semantic threshold 继续硬编码，确实会形成隐性耦合。不同 embedding 模型的余弦分布不同，阈值跟着模型变动是合理设计。

#### 2. `semantic_threshold()` 的“调用时读取配置”优于 import-time 常量

这让：

- 标定后只需改 `.env`
- 测试里可 monkeypatch
- 不需要通过重新发版修改阈值

这一步是值得肯定的。

#### 3. `calibrate_threshold()` 抽成纯函数是这次改动里最有价值的一步之一

它把两件事拆开了：

- **算法正确性**：可在本地/CI/沙箱里验证
- **真实 embedding 分布采样**：才依赖真实 API key

这比把所有断言都塞进一个“没 key 就永远 skip”的用例里要强得多。

#### 4. `tests/test_faq_semantic.py` 本轮新增测试确实更完整

本轮测试不再只做“同义问句分高于阈值”这种单边断言，而是同时覆盖：

- positives 与 negatives 两组分布
- 阈值是否处于间隙内
- 命中的是不是**正确 FAQ**
- synthetic calibration harness 是否真的能抓错

这类测试对 semantic FAQ 这种容易“看起来工作、实际上误命中”的功能非常关键。

### 负面/保留结论

本次核心后端逻辑整体未见明显 correctness 回归；真正需要优先处理的，反而仍是商城前端的旧问题尾巴，以及新配置面的边界约束。

---

## 四、已确认问题

# 问题 1：商城页“登录后自动补单”仍存在异步竞态，可导致未来登录时幽灵下单

**级别：高**

### 位置

- `app/templates/shop.html:191-215`
- `app/static/auth.js:121-131`

### 现象概述

当前版本已经修掉了最直观的“监听器永久残留”问题：

- `CodeMaxAuth.onChange()` 现在会返回退订函数
- `notify()` 会遍历监听器副本
- `shop.html` 中也增加了 `offBuy` 防止重复登记

这些都说明修复方向是对的。

但仍有一个**请求乱序**场景没有封死：

> 某个较早发出的 `/shop/orders` 请求，如果很晚才返回 `401`，而这时用户其实已经登录、甚至后续另一次下单已经成功，那么这个“迟到的 401”仍会把旧购买意图重新挂回监听器列表。

之后用户未来某次再登录，就可能**无点击地再次触发 `buy()`**。

### 关键代码机制

`shop.html` 当前逻辑核心片段：

- 先 `fetch("/shop/orders")`
- 若 `401`：
  - `CodeMaxAuth.open("login")`
  - 如果 `offBuy` 已存在就不重复登记
  - 否则注册一个 `CodeMaxAuth.onChange(...)`
  - 登录成功时自动补发一次 `buy()`

问题在于它**没有识别这个 401 是否已经过时**。

### 我做过的复现

我把当前 `shop.html` 与 `auth.js` 的关键控制流抽出来，用 **Node** 真实模拟了以下时序：

1. 用户未登录，点第一次购买，请求 A 发出
2. 用户先完成登录
3. 用户又点一次购买，请求 B 成功
4. 旧请求 A 这时才迟到返回 `401`
5. A 把一个新的待补单监听器挂回去
6. 用户将来某次再登录，旧意图被重放，再次触发 `buy()`

实测输出关键点：

```text
order created via second click
after success listeners= 0 offBuy? false fetchCalls= 2
open login
after late 401 listeners= 1 offBuy? true fetchCalls= 2
after relogin listeners= 0 offBuy? false fetchCalls= 3
order created via replayed
```

`fetchCalls` 从 `2` 变成 `3`，说明未来登录确实触发了**额外一次下单请求**。

### 影响

这是业务级 bug，不是纯前端风格问题：

- 用户未来某次登录可能突然多出一张 pending 订单
- 会污染“同一用户最多一张 pending 单”的业务状态
- 会给用户造成“我没点购买却出现待支付订单”的体验错误
- 排查困难，因为触发时机与原始购买点击已被时序拉开

### 为什么现有测试没抓到

当前测试覆盖的是顺序路径，但**没有制造“旧 401 晚到返回”**这种 out-of-order 情况。

所以：

- 监听器重复登记的静态问题已被抓住并修复
- 但“晚到旧请求复活监听器”的动态竞态仍漏网

### 建议修复方向

至少需要增加一种“旧请求失效”机制，例如：

1. 为购买尝试分配递增序号，只接受最新 attempt 的 401
2. 或在 auth state / login generation 上做版本戳，旧 generation 返回的 401 不得登记补发监听器
3. 或在 401 分支先检查当前是否已登录，若已登录则忽略该 401，而不是登记未来监听器

同时建议补一条真正的前端乱序时序测试。

---

# 问题 2：`LLM_SEMANTIC_THRESHOLD` 没有做 `[0,1]` 范围校验，误配会静默破坏 FAQ 级联

**级别：中**

### 位置

- `app/config.py:24-29`
- `.env.example:18-24`

### 问题描述

这次将语义阈值改成配置项，方向是正确的；但配置一旦暴露给 `.env`，就应同时约束输入范围。

当前定义仅为：

```python
LLM_SEMANTIC_THRESHOLD: float = 0.55
```

没有 `ge=0.0, le=1.0` 或等价 validator。

### 我实际验证过

使用 `Settings()` 读取环境变量时，以下非法值都会被接受：

```text
1.5 -> 1.5
-0.1 -> -0.1
```

### 风险

- 若设成 **大于 1**：语义检索几乎永远不会命中，等同于静默关闭 semantic FAQ
- 若设成 **小于 0**：几乎所有非负余弦都可能被视作命中，FAQ 会过度误答

第二种尤其危险，因为它会把本应转人工或继续级联到 LLM 的问题错误短路成 FAQ。

### 为什么这是本次提交范围内问题

在阈值还是代码常量时，不存在“运维误配”入口；本次提交恰好把它变成配置项，因此也同步引入了配置面风险。

### 建议

- 用 `Field(0.55, ge=0.0, le=1.0)` 或 validator 约束范围
- 增加一条针对配置非法值的测试，确保启动期直接失败，而不是线上静默行为异常

---

# 问题 3：文档与工程说明存在系统性漂移，且分布在多个目录

**级别：中**

这不是单个 README 的小误差，而是跨目录、多文件的**系统性失配**。由于本仓库高度依赖说明文档做维护导航，这类漂移会直接影响后续审查、接手、排障和变更同步。

下面按目录分组列出。

---

## 4.1 `.github/workflows/`：README 仍写 CI 只有 3 个 job，但实际已有 4 个

### 实际情况

我用 YAML 解析 `ci.yml` 实测得到：

- `lint`
- `docs`
- `test-sqlite`
- `test-postgres`

即当前实际是 **4 个 job**。

### 仍然写旧值的位置

- `.github/workflows/README.md:20-23`
- `总览.md:130`
- `总览.md:454`

这些位置仍写成 **3 个 job**。

### 影响

会误导维护者忽略 `docs` job，也会让“文档站现在是否进 CI”这个事实被写反。

---

## 4.2 `tests/test_docs_site.py` 注释仍写“CI 不构建文档站”，但当前 `ci.yml` 已有 docs job

### 位置

- `tests/test_docs_site.py:1-15`

### 现状

文件头注释仍写：

- `scripts/build_docs_site.py` 以前零测试
- `CI 不构建文档站`

但当前 `.github/workflows/ci.yml` 已明确存在：

- `docs` job
- 安装 `mistune`
- 生成数据并渲染整站
- 校验生成页数

### 影响

这会让后来人误判该测试与 CI 的关系，也会让调试 docs 相关问题的人误以为“线上没人跑构建”。

---

## 4.3 `app/config.py` / `.env.example` 已是 41 项配置，但多处文档仍写 38 项

### 实际情况

我直接读取 `Settings.model_fields`，实测：

- **41 项配置**

### 仍写旧值的位置

- `app/README.md:22,81`
- `docs/ROOT_FILES.md:31,276,328`
- `总览.md:33,172,384`
- `docs/ARCHITECTURE_GUIDE.md:204`

### 影响

这种数字漂移在本仓库里不是小问题，因为多份“目录总览”都把它当作结构真相引用。

---

## 4.4 `app/static/` 已不再只有 `er.js` 一个文件，但说明文档仍按旧结构书写

### 实际目录

当前 `app/static/` 包含：

- `README.md`
- `auth.js`
- `er.js`
- `pay_qr.svg`

### 仍写旧值的位置

- `app/static/README.md:15`
- `app/static/README.md:24`
- `app/static/README.md:41`
- `总览.md:154`
- `总览.md:450`

仍将其描述为“只有一个文件：`er.js`”。

### 影响

会直接误导维护者漏看 `auth.js`；而本轮发现的高优先级 bug 恰恰与 `auth.js` / `shop.html` 协作有关。

---

## 4.5 `app/templates/` 实际为 8 个 HTML 模板，但文档仍写 7 个

### 实际目录

当前模板文件有：

- `base.html`
- `drawio.html`
- `er.html`
- `index.html`
- `mermaid.html`
- `mock_pay.html`
- `oauth_consent.html`
- `shop.html`

### 仍写旧值的位置

- `app/templates/README.md:16`
- `README.md:223`
- `总览.md:449`

### 影响

会使模板层的结构认知落后，尤其会让 `shop.html` 这类重要页面在总览层面被低估。

---

## 4.6 docs-site 统计值已变化，但 `docs/site/README.md` / `scripts/README.md` 仍保留旧值

### 当前真实值

本轮实测：

- `77 modules`
- `204 edges`
- `39 routes`
- `268 symbols`
- `21 docs`
- `10066 doc lines`

### `docs/site/README.md` 的旧值

- `docs/site/README.md:158-160` 仍写 `72 个模块 / 179 条边 / 38 条路由 / 250+ 符号`
- `docs/site/README.md:167-169` 仍写“实际是 16 条需鉴权”，但当前 `routes.json` 实测是 **18**
- `docs/site/README.md:185` 仍写 `198 条依赖边 / 265 个符号`

### `scripts/README.md` 的旧值

- `scripts/README.md:21` 仍写 `build_docs_site.py` 为 `898` 行
- `scripts/README.md:49` 改成了 `935` 行，但仍写成 `32 个函数`
- `scripts/README.md:255` 仍描述提取结果为 `72 模块 / 179 条边`
- `scripts/README.md:331` 仍写 `行数 898 / 函数 35`
- `scripts/README.md:336-337` 仍写 `198 条边`，文档行数示例仍是 `9345`

### 影响

这些文档本来承担“如何复核 docs-site 构建”的角色，一旦它们本身和当前真实输出不一致，维护者很难再信任复核步骤。

---

## 4.7 `app/tools/README.md` 仍引用已删除/已更名的旧语义阈值常量名

### 位置

- `app/tools/README.md:57`

### 现状

该处依赖表仍写：

- `SEMANTIC_CONFIDENCE_THRESHOLD`

但当前代码实际使用的是：

- `semantic_threshold()`
- 配置项 `LLM_SEMANTIC_THRESHOLD`

### 影响

这会让阅读说明文档的人去搜一个已经不存在的名字，属于典型的“文档引用漂移”。

---

## 4.8 测试规模相关文档未跟当前真实规模同步

### 当前真实值

本轮实测：

- `37` 个 tests 目录下 `.py` 文件
- `35` 个 `test_*.py`
- `8431` 行测试代码
- `488` 个 `def test_...`
- SQLite：`541 passed, 4 skipped`
- PG：`543 passed, 2 skipped`

### 仍写旧值的位置

- `tests/README.md:17-19` 仍写 `7994 行 / 473 个测试函数 / 530 条`
- `tests/README.md:375` 仍再次引用 `473 个测试函数`
- `docs/ARCHITECTURE_GUIDE.md:189,206` 仍写 `530 条自动化测试`

### 影响

这类数字本来常被拿来做“仓库规模感知”和“测试覆盖面概览”，长期失真会拖累后续审查质量。

---

## 4.9 `.claude/skills/` 中仍有旧测试基线和旧指引

### 覆盖结果

本轮我逐个查看了 `.claude/skills/` 下各子目录。

### 仍保留旧值的代表性位置

- `.claude/skills/new-tool-page/SKILL.md` 的验证段仍写全量 `418 passed, 2 skipped`
- `.claude/skills/schema-sync/SKILL.md` 仍写 `418 passed, 2 skipped` / `419 passed, 1 skipped`

### 影响

如果这些 skill 文件也被当作“开发 checklist”，那它们的过期会把旧基线继续传播到后续变更流程中。

---

## 4.10 根目录历史审查产物仍会污染搜索

### 位置

- `review_report_arena_01a0599b_r3.md`

### 问题

这是历史审查快照，里面保留了大量当时的旧统计与旧判断。若不明确区分其“历史性质”，继续做全仓 `grep` 时很容易把它误当成当前事实来源。

### 说明

这不一定要删，但至少要明确“历史报告仅供存档”，否则会持续放大数字漂移问题。

---

## 五、按目录的完整覆盖结论

下面给出一个“一个文件夹不落”的汇总表。

| 目录/区域 | 本轮检查内容 | 结论 |
| --- | --- | --- |
| 根目录文件 | `README.md`、`AGENTS.md`、`HANDOVER.md`、`TECH_DECISIONS.md`、`ROADMAP.md`、`Dockerfile`、`docker-compose.yml`、`requirements.txt`、`pytest.ini`、`main.py`、`.env.example` 等 | 运行与依赖总体正常；存在多处统计/结构说明漂移；新增阈值配置缺范围校验 |
| `.claude/skills/` | 7 份 skill 文档全部过了一轮 | 存在旧测试基线残留，尤其 `new-tool-page`、`schema-sync` |
| `.github/workflows/` | `ci.yml` + `README.md` | `ci.yml` 正常，docs job 已存在；README 仍写 3 job，已过期 |
| `app/` 根层 | 配置、数据库、安全、限流、存储、启动检查 | 未发现本轮新增后端硬回归；`config.py` 新阈值缺边界校验 |
| `app/routers/` | 路由清单、鉴权、限流、页面路由等 | 实测 39 条业务 method-path、34 个唯一 path；未见新增 correctness 问题 |
| `app/static/` | `auth.js`、`er.js`、`pay_qr.svg`、README | 找到与 `shop.html` 协作的高优先级竞态 bug；README 严重过期 |
| `app/templates/` | 全部模板，重点 `shop.html` | `shop.html` 存在幽灵补单竞态；其余未见本轮明显新破坏 |
| `app/tools/` | FAQ、semantic、intent、support、crawler 等 | 本次 semantic FAQ 改动总体靠谱；README 有旧常量名残留 |
| `database init/` | `full_init.sql`、迁移、初始化脚本 | fresh PostgreSQL 验证通过；未见 schema/执行层问题 |
| `docs/` | 架构、部署、Windows、本地运行、ROOT_FILES | 大量统计值与结构说明已落后 |
| `docs/site/` | `README.md`、`style.css`、`site.js` | 构建功能正常，但 README 多项统计值与当前输出不符 |
| `docs/site/data/` | `meta.json`、`routes.json` 等 | 当前最可信的统计真值来源 |
| `scripts/` | `build_docs_site.py`、`check_schema_pg.mjs`、README | 脚本实跑正常；README 对尺寸/函数数/输出统计有多处旧值 |
| `tests/` | 全量 suite + 重点文件代码复核 | 全量通过；`test_faq_semantic.py` 增强质量高；目录说明文档和部分注释过期 |

---

## 六、没有发现的问题 / 本轮确认正常的点

为了避免报告只列问题，这里明确列出本轮确认正常或已改进的部分。

### 1. 本次 semantic FAQ 后端主链路未见明显 correctness 回归

我复核 `faq.py` / `support.py` / `test_faq_semantic.py` 后的判断是：

- 改动逻辑自洽
- 配置化方向合理
- synthetic harness 的引入显著提高了“标定逻辑真被执行到”的可信度

### 2. SQLite 与 fresh PostgreSQL 同时全绿，说明本次提交没有引入明显 DB 分歧

结果分别为：

- SQLite：`541 passed, 4 skipped`
- PG：`543 passed, 2 skipped`

因此，本轮**没有**观察到“SQLite 绿、PostgreSQL 红”的结构性回归。

### 3. docs-site 构建脚本当前实际可运行

不仅 `--data-only` 通过，整站渲染也通过；生成页数与结构都正常。

### 4. CI 中 docs job 已实际存在

这意味着“文档站无人构建”的旧状态已经被改善；问题在于相关说明文档尚未跟上，而不是 CI 本身缺失。

---

## 七、优先级建议

### P1

1. **修复商城登录后自动补单竞态**
   - 这是当前唯一明确的真实行为级 bug
   - 已有修复只覆盖顺序场景，未覆盖旧 401 晚到场景
   - 建议同时补回归测试

### P2

2. **为 `LLM_SEMANTIC_THRESHOLD` 增加范围校验**
   - 避免 `.env` 误配静默破坏 FAQ 分流
   - 这是本次配置化改动应顺手补完的边界保护

### P3

3. **做一次仓库级文档数字同步**
   优先同步这些文件：
   - `.github/workflows/README.md`
   - `tests/test_docs_site.py`
   - `app/static/README.md`
   - `app/templates/README.md`
   - `app/tools/README.md`
   - `docs/site/README.md`
   - `scripts/README.md`
   - `tests/README.md`
   - `docs/ARCHITECTURE_GUIDE.md`
   - `app/README.md`
   - `docs/ROOT_FILES.md`
   - `总览.md`
   - `.claude/skills/new-tool-page/SKILL.md`
   - `.claude/skills/schema-sync/SKILL.md`

### P4

4. **明确历史 review 报告的归档属性**
   - 避免它们继续污染仓库内 grep / 数字核对结果

---

## 八、最终结论（可直接转述）

如果需要一句适合反馈给对方 assistant 的结论，我建议这样表述：

> 这次 `80a7b87` 的 semantic FAQ / 阈值配置化补丁整体方向正确，核心后端逻辑与新增测试质量都不错，SQLite 和 fresh PostgreSQL 全量验证也都通过；但当前 tip 仍有一个可复现的商城前端竞态 bug（登录后自动补单在旧 401 晚到时会留下未来登录触发的幽灵下单），并且 `LLM_SEMANTIC_THRESHOLD` 作为新配置项还缺少 `[0,1]` 范围校验。此外，仓库内多目录文档与工程说明存在系统性统计漂移，尚未完全收口。因此，这一版可以评价为“明显进步、主体可取”，但还不宜判定为“全部彻底完成”。

---

## 九、下载说明

本报告已保存为：

- `review_report_arena_01a0599b_r5.md`

静态文件服务应从仓库根目录提供该文件；若通过浏览器访问工作区服务，可直接下载同名文件。
