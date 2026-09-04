# `app/tools/` 模块说明书

> **行号基准 commit：`3501db8`**（2026-09-04）。
>
> 姊妹篇：`app/routers/README.md`（HTTP 接口层，谁在调用本层）、`app/README.md`（根级基础设施，本层的地基）、
> `database init/README.md`（建表脚本）、`app/templates/README.md`（前端模板）。本文所有 `L12-L35` 形式的引用都对应这个提交。
> 代码一改行号就会漂 —— 复算方式见文末「附：行号与数字怎么复核」。

---

## 1. 模块概述

### 1.1 定位

`app/tools/` 是本项目的**业务逻辑层**（`ARCHITECTURE_GUIDE.md` 第 1 课里的「后场工坊」）。

`app/routers/` 只做 HTTP 层的事（鉴权、限流、状态码映射、序列化），**所有真正干活的代码都在这里**。这条边界写在 `AGENTS.md` 的「目录约定」里，是硬约束。

10 个文件、1 625 行，按功能分成三条互不相干的链：

| 链 | 文件 | 干什么 | ROADMAP |
| --- | --- | --- | --- |
| **A. ER 图与数据字典** | `sql_ddl.py` → `word.py`、`llm.py` | SQL 建表语句 → ER 图数据 → Word 文档；自然语言 → Mermaid 类图 | S2-01 |
| **B. 智能客服** | `faq.py` → `intent.py` → `support.py` | FAQ 检索 → 意图路由 → 三层编排（FAQ / 闲聊 / RAG）+ 转人工兜底 | S4-02 |
| **C. 内容抓取** | `politeness.py` → `crawler.py` → `extract.py`、`browser.py` | robots 礼仪 → SSRF 校验 + 抓取 → LLM 指认选择器 → 入库；SPA 站点用无头浏览器兜底 | S4-01 |

三条链之间**没有相互依赖**（唯一的跨链复用是 `support.py` 复用 `faq.py` 的检索索引，两者同属 B 链）。

### 1.2 依赖关系

**内部（`app/tools/` 之间）：**

```
                    ┌── politeness.py ◀──────────┐
                    │         ▲                  │
   llm.py ◀─────────┼── crawler.py ◀──── browser.py
     ▲              │         ▲
     │              │         │
     ├── extract.py ┘─────────┘
     │
     ├── faq.py ◀── intent.py ◀── support.py ──▶ llm.py
     │      ▲                          │
     │      └──────────────────────────┘（复用 _Index 做 RAG 检索）
     │
   sql_ddl.py      word.py        （A 链两个文件互不依赖）
```

实测的 import 语句（`grep -n "^from \.\|^from \.\." app/tools/*.py`）：

| 文件 | 依赖的兄弟模块 | 行号 |
| --- | --- | --- |
| `browser.py` | `politeness`（整模块）、`crawler`（`MAX_BYTES`/`TIMEOUT`/`USER_AGENT`/`CrawlError`/`Page`/`_request`/`assert_public_url`） | L25-L26 |
| `crawler.py` | `politeness`（整模块） | L33 |
| `extract.py` | `crawler`（`fetch`/`to_skeleton`）、`llm`（`LLMClient`/`LLMError`/`default_llm`） | L27-L28 |
| `intent.py` | `faq`（`search`） | L24 |
| `support.py` | `faq`（`_Index as RetrievalIndex`、`tokenize`、局部导入 `search`）、`intent`、`llm` | L27-L30、L150 |
| `politeness.py` | `crawler`（**函数内延迟导入** `CrawlError`，避免循环） | L114 |
| `sql_ddl.py` / `word.py` / `llm.py` / `faq.py` | 无（叶子模块） | — |

**唯一的循环依赖**：`crawler` ⇄ `politeness`。`crawler.py:33` 在模块级导入 `politeness`，而 `politeness.py:114` 在 `_load_robots()` **函数体内**导入 `CrawlError` —— 延迟导入把环断开了，理由写在 L112-L113 的注释里。

**外部（谁在用 `app/tools/`）：**

| 调用方 | 用了什么 | 行号 |
| --- | --- | --- |
| `app/routers/tools.py` | `llm`（`generate_mermaid`/`get_llm`/`LLMError`）、`sql_ddl`（`parse_ddl`）、`word`（`build_data_dictionary`/`FILENAME`/`MIME_DOCX`） | L9-L11 |
| `app/routers/support.py` | `support.answer` | L12 |
| `app/routers/admin.py` | `browser.render`、`crawler.CrawlError`、`extract`（4 个符号）、`llm`（`get_llm`/`LLMError`）、`politeness.RobotsDisallowed` | L21-L25 |

**对上层包的依赖**：`llm.py:16` → `..config.settings`；`extract.py:26` 与 `support.py:26` → `..models.Article`。

---

## 2. 文件级详细说明书

### 📄 文件名：`__init__.py`

- **文件职责**：空文件，只用于把 `app/tools` 标记成包。
- **核心类/函数清单**：无。
- **关键变量/常量**：无。

---

### 📄 文件名：`sql_ddl.py`（302 行）

- **文件职责**：把 SQL 建表语句解析成 ER 图数据 `{"tables": [...], "edges": [...]}`，可直接喂给前端 D3.js 渲染。**不引入第三方解析库**，用正则 + 单遍字符扫描实现，兼容 MySQL 与 PostgreSQL 常见写法。

#### 关键变量/常量（L29-L36）

| 常量 | 行 | 作用 |
| --- | --- | --- |
| `_QUOTES` | L29 | 三种引号 `'` `"` `` ` ``。判断「首词是不是被引号包住的标识符」全靠它 |
| `_IDENT` | L30 | 标识符正则，允许 `` `col` `` / `"col"` 与 `schema.table` 前缀 |
| `_CREATE_TABLE` | L31 | 匹配 `CREATE TABLE`（含 `IF NOT EXISTS`），忽略大小写 |
| `_TYPE` | L32 | 匹配列类型，**只取首词**（`DOUBLE PRECISION` → `DOUBLE`，已知简化，见模块 docstring） |
| `_QUOTED` | L34 | 单引号字面量正则，支持 MySQL 反斜杠转义 `\'` 与 SQL 标准 `''` 双写 |
| `_CONSTRAINT_HEADS` | L35 | 8 个约束关键字集合，用于区分「这是一列」还是「这是一条表级约束」 |
| `_ESCAPED` | L36 | MySQL 转义字符映射表（`\n` `\t` `\r` `\b` `\0` `\Z`） |

#### 核心函数清单

**`parse_ddl(sql: str) -> dict`　L39-L49**
- 输入：`sql` —— 原始 DDL 文本
- 返回：`{"tables": [...], "edges": [...]}`
  - `tables[i]` 的键：`name` / `columns` / `comment`
  - `columns[i]` 的键：`name` / `type` / `primary_key` / `nullable` / `default` / `comment`
  - `edges[i]` 的键：`from_table` / `from_column` / `to_table` / `to_column`
- 逻辑：L41 剥注释 → L44-L47 逐张表解析并累积边 → L48 补 PostgreSQL 风格注释 → L49 组装返回。

**`_scan(s: str)`　L52-L96**（生成器，整个模块的地基）
- 产出三元组 `(下标, 字符, 是否在字符串字面量内)`。
- L60-L75：**在字符串内**的分支 —— L61-L66 处理 MySQL 反斜杠转义（反引号内不适用），L67-L72 处理同字符双写转义（`''` `""` ``` `` ```），L73 遇配对引号则退出字符串状态。
- L76-L79：遇引号则进入字符串状态。
- L80-L84：`--` 或 `#` 到行尾的**行注释**，整段折叠成一个空格。
- L85-L89：`/* */` **块注释**，同样折叠成空格；未闭合时吃掉剩余全部。
- L90-L91：普通字符原样产出。
- **为什么必须有它**：模块 docstring L6-L21 列了 7 种会解析错乱的写法（`COMMENT 'it\'s'`、`DEFAULT 'a--b'`、字符串里的 `CREATE TABLE` 等），每种都有回归测试。所有结构性判断都必须建立在「认得字符串与注释」之上。

**`_strip_comments(sql)`　L99-L101** / **`_in_string_positions(sql)`　L104-L106**
- 前者把注释剥掉、字面量内容原样保留；后者返回「字符串内部字符的下标集合」，供调用方跳过字符串里的 SQL 关键字。两者都是 `_scan` 的薄封装。

**`_iter_tables(sql)`　L109-L121**（生成器）
- L110 先算出字符串位置集合 → L111 遍历所有 `CREATE TABLE` 匹配 → L112-L113 **若匹配起点在字符串内就跳过**（防「幻影表」）→ L115-L116 匹配表名与左括号 → L117-L119 读配对括号内容并产出 `(表名, 列定义体)`。

**`_read_balanced(s)`　L124-L136**
- 从 `s[0] == '('` 起读配对括号，**忽略字符串内的括号**（L127-L128）。L131-L134 深度归零时返回 `(内容, 结束下标)`；L135 未闭合返回 `None`。

**`_split_top_level(body)`　L139-L154**
- 按**顶层**逗号切分列定义。L142-L150 遍历时维护括号深度，只有在 `depth == 0` 且不在字符串内的逗号才切分（L146）。L153 去掉空白片段。

**`_parse_table(name, body)`　L157-L178**
- L162 遍历切分后的每一段 → L163 取首词 → **L165 是关键分支**：首词**不带引号**且大写后属于 `_CONSTRAINT_HEADS` 才当表级约束（L166），否则当列定义（L168）。带引号的 `` `key` `` / `"index"` 因此不会被误判成约束关键字。
- L174-L177：把表级 `PRIMARY KEY` 里列出的列回填成 `primary_key=True` / `nullable=False`。
- L178 返回 `({"name","columns","comment": None}, edges)` —— `comment` 先留空，由 `_apply_comments` 后填。

**`_parse_column(part, table)`　L181-L216**
- L182-L184 拆出列名与剩余部分，拆不出返回 `(None, None)`。
- L187-L189 匹配类型，失败返回 `(None, None)`。
- L192-L196 分别找 `DEFAULT`、`COMMENT`、`PRIMARY KEY`。
- L198-L205 组装列字典：L200 类型去空白并大写；L202 `nullable` = 非主键且没写 `NOT NULL`。
- L207-L215 找行内 `REFERENCES`，有则顺带产出一条外键边。

**`_parse_constraint(part, table, edges, pk_cols)`　L219-L239**
- L221-L223 `PRIMARY KEY` → 把括号里的列名塞进 `pk_cols` 后直接返回。
- L224-L227 匹配表级 `FOREIGN KEY (...) REFERENCES t (...)`，匹配不到就返回。
- L230-L238 把源列与目标列**逐对**配成边。L232 用 `zip(..., strict=False)`：用户写错列数时按短的一边配对，**尽力出图而不是抛错**。

**`_apply_comments(sql, tables)`　L242-L265**
- 处理 PostgreSQL 的 `COMMENT ON TABLE / COLUMN ... IS '...'`。L245-L246 同样跳过字符串内的匹配；L250-L253 填表注释；L254-L265 按 `表.列` 拆开填列注释。

**`_paren_list(m)`　L268-L271** / **`_unquote(s)`　L274-L278** / **`_unescape(s)`　L281-L297** / **`_short(name)`　L300-L302**
- 四个小工具：括号列表拆项、去引号、还原转义、去 schema 前缀（`public.sys_user` → `sys_user`）。

---

### 📄 文件名：`word.py`（51 行）

- **文件职责**：把 `parse_ddl` 的输出写成 Word 数据字典（`.docx` 字节）。**纯函数，不碰 HTTP**。用 `python-docx`（不是路线图原先写的 Java 库 Apache POI）。

#### 关键常量

| 常量 | 行 | 值 / 作用 |
| --- | --- | --- |
| `MIME_DOCX` | L12 | `application/vnd.openxmlformats-officedocument.wordprocessingml.document`，供路由层设 `Content-Type` |
| `FILENAME` | L13 | `data_dictionary.docx`，下载文件名 |
| `_HEADERS` | L15 | 表格 6 个列头：字段 / 类型 / 主键 / 可空 / 默认值 / 注释 |

#### 核心函数

**`build_data_dictionary(graph: dict) -> bytes`　L18-L51**
- 输入：`graph` —— `parse_ddl` 的返回值
- 返回：`.docx` 的原始字节
- 逻辑：
  - L19-L21 建文档、加 0 级标题、写一行统计（几张表、几条外键）
  - L23-L37 每张表一节：L24 表名后缀带表注释；L25-L26 建表格并套 `Table Grid` 样式；L27-L29 写表头并加粗；L30-L37 逐列填 6 个单元格（L33 主键用 `✔`；L34 可空取反显示「是/否」；L35 默认值为 `None` 时留空）
  - L39-L45 有外键时追加一节，每条边一行项目符号（`a.b → c.d`）
  - L47-L50 存进 `BytesIO` 并返回字节

---

### 📄 文件名：`llm.py`（105 行）

- **文件职责**：OpenAI 兼容的 LLM 客户端 + 自然语言转 Mermaid 类图。**客户端必须可注入**，否则测试会真打网络（模块 docstring L3-L4）。

#### 关键常量

| 常量 | 行 | 作用 |
| --- | --- | --- |
| `_DIAGRAM_TYPES` | L19-L27 | 7 种 Mermaid 图类型首关键字。模型有时返回 `erDiagram` 而非 `classDiagram`，都放行 |
| `SYSTEM_PROMPT` | L29-L37 | UML 建模提示词，5 条硬性要求（首行必须 `classDiagram`、不要围栏、类名大驼峰、属性写 `+类型 名称`、信息不足时补全不反问） |
| `_FENCE` | L39 | ```` ``` ```` 围栏正则，**闭合与未闭合都能吃下**（模型常忘记收尾） |

#### 核心类/函数

**`class LLMError(RuntimeError)`　L42-L43** —— LLM 调用失败的统一异常。

**`@dataclass class LLMClient`　L47-L78**
- 字段：`api_key`（默认空串）、`base_url`（默认 `https://api.openai.com/v1`）、`model`（默认 `gpt-4o-mini`）、`timeout`（60.0 秒）、`transport`（`httpx.AsyncBaseTransport | None`，**测试注入 `MockTransport` 用**，L52）
- **`async chat(self, system: str, user: str) -> str`　L54-L78**
  - 输入：system / user 两段提示词；返回：模型回复正文
  - L55-L56：没配 `api_key` 直接抛 `LLMError`（不发请求）
  - L57-L71：POST `{base_url}/chat/completions`，`temperature=0.2`，两条 message；L70-L71 把 `httpx.HTTPError` 包成 `LLMError`
  - L72-L73：非 200 抛 `LLMError`，带上状态码与响应前 200 字符
  - L74-L78：取 `choices[0].message.content`；L76-L78 捕获 `KeyError`/`IndexError`/`TypeError`/`ValueError` 四种，转成「返回体不符合预期」

**`default_llm`　L81-L85** —— 从 `settings` 读 `LLM_API_KEY` / `LLM_BASE_URL` / `LLM_MODEL` 构造的默认实例。

**`get_llm() -> LLMClient`　L88-L90** —— FastAPI 依赖入口，测试用 `app.dependency_overrides` 替换。

**`async generate_mermaid(text, llm=default_llm) -> str`　L93-L99**
- L94 调 `chat` → L95 去围栏 → **L96-L97 校验首关键字**，不是 7 种图类型之一就抛 `LLMError`（带实际输出前 120 字符）。

**`_strip_fence(reply) -> str`　L102-L105** —— 去掉 ```` ```mermaid ```` 围栏只留图代码。

---

### 📄 文件名：`faq.py`（198 行）

- **文件职责**：FAQ 检索，**BM25 + 余弦相似度融合召回**。语料硬编码在代码里（12 条），不放数据库 —— 条目少、随代码评审、不需要运营后台（L32-L34 注释）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `BM25_WEIGHT` | L22 | `0.6` | 融合权重：BM25 占 0.6，余弦占 0.4 |
| `_K1` | L23 | `1.5` | BM25 词频饱和参数 |
| `_B` | L24 | `0.75` | BM25 文档长度归一化强度 |
| `_BM25_SATURATION` | L158 | `3.0` | 把无上界的 BM25 压进 `[0,1)` 的饱和参数，按现有语料实测分布标定（TD-151） |

**L20 `jieba.initialize()`** —— 模块导入时就预热词典。jieba **首次**分词要加载词典（实测约 560 ms），不预热就会算进第一个用户的请求里。

**`FAQS`　L36-L66** —— 12 条 `Faq` 记录（实测 `len(FAQS) == 12`）。

#### 核心类/函数

**`@dataclass(frozen=True) class Faq`　L28-L31** —— 三个字段：`q`（标准问法）、`a`（答案）、`keywords`（额外召回词，默认空元组）。

**`tokenize(text) -> list[str]`　L69-L75** —— jieba 精确模式分词 + 过滤空白。**刻意不做停用词过滤**：语料只有十几条，滤掉「怎么」「可以」反而会让「怎么收费」和「怎么退款」无法区分（L71-L74 docstring）。

**`class _Index`　L78-L136** —— 语料的 BM25 与 TF-IDF 统计量，**构造一次、查询多次**。
- **`__init__(self, docs: list[list[str]])`　L81-L104**
  - L82-L85 存文档、算文档数 `n`、各文档长度、平均长度 `avgdl`
  - L87-L92 建 `tf`：词 → 每篇出现次数
  - L94-L97 建 `df`：词 → 出现过该词的文档数
  - **L101-L104 是关键**：TF-IDF 向量 `vec` 与它的模长 `norm`。L99-L100 的注释记了一次真实 bug（TD-152）：早先分子用 `tf·idf` 点积、分母却用裸 `tf` 的模长，算出的「余弦」能超过 1（实测 1.050），阈值无从标定
- **`_idf(self, term) -> float`　L106-L109** —— Robertson 形式 IDF，`log(1 + (n-df+0.5)/(df+0.5))`，**恒为正**，避免高频词出现负权重
- **`bm25(self, query) -> list[float]`　L111-L122** —— 对每篇文档累加各查询词的 BM25 贡献；L118 分母含文档长度归一化项
- **`cosine(self, query) -> list[float]`　L124-L136**
  - L125-L127 统计查询词频
  - **L130 是关键**：查询向量同样带 `idf`（与文档向量同量纲），且**丢掉语料里没有的词**（它们对点积无贡献，留在分母只会把余弦整体压低）
  - L131-L135 逐篇算点积除以两个模长，结果严格落在 `[0,1]`

**`_corpus_tokens()`　L139-L141** / **`_INDEX`　L144** —— 每条 FAQ 的可检索文本 = 标准问法 + 额外召回词 + 答案；模块级构造一次索引。

**`@dataclass(frozen=True) class FaqHit`　L148-L153** —— 5 个字段。**注意两个分数的语义差别**（L150 与 L152 的注释）：
- `score` —— **同一结果集内**归一化，只用于排序，**不能跨查询比较**（top1 恒为 1.0）
- `confidence` —— 由未归一化的原始分算出的**绝对**置信度 `[0,1]`，**可以**跨查询比较

**`_saturate(x)`　L161-L163** —— `x/(x+3.0)`，把无上界的 BM25 单调压进 `[0,1)`，`x=0` 时为 0。

**`_normalize(scores)`　L166-L171** —— 按最大值归一化；全 0 时返回全 0（避免除零）。

**`search(query, k=3) -> list[FaqHit]`　L174-L198**
- L176-L178 分词，空则返回 `[]`
- L179-L180 取两路**原始**分
- L184-L185 各自按最大值归一化
- L186 融合：`0.6·bm25 + 0.4·cosine`
- L187 按融合分降序排下标
- L188-L198 取 top-k 组装 `FaqHit`，**L194 的 `confidence` 用的是原始分**（`_saturate(raw_bm)` 与 `raw_cos`），L197 过滤掉融合分为 0 的
- **L181-L183 的注释是一条血泪教训**：拿 `score` 当绝对置信度会让「python 部署 nginx 报错」也判成 FAQ 命中

---

### 📄 文件名：`intent.py`（112 行）

- **文件职责**：把用户输入分成 **FAQ / 闲聊 / 专业问题** 三类，交给 `support.py` 分流。

> **模块 docstring L3-L18 明确交代：ROADMAP 里写的「训练或微调 BERT 多分类模型」没有做，也不假装做了** —— 缺标注数据、缺 GPU、缺预训练权重，写出来的「模型」无法验证。所以这里给的是**可替换的路由接口 + 一个确定性实现**，代价记在 TD-150。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `FAQ_CONFIDENCE_THRESHOLD` | L40 | `0.40` | FAQ 的**绝对**置信度门槛。**在现有 12 条语料上实测标定**（L34-L38 记了标定值），语料变大必须重标（TD-151） |
| `PROFESSIONAL_KEYWORDS` | L43-L48 | **31 个**技术词 | 命中即倾向判为专业问题 |
| `CHITCHAT_KEYWORDS` | L51-L53 | **9 个**寒暄词 | 只在没命中 FAQ、也没技术词时才用得上 |

#### 核心类

**`class Intent(str, Enum)`　L27-L30** —— 三个取值：`FAQ="faq"` / `CHITCHAT="chitchat"` / `PROFESSIONAL="professional"`（实测确认）。

**`@dataclass(frozen=True) class IntentResult`　L57-L60** —— `intent` / `confidence`（`[0,1]`）/ `reason`（判定依据，**答辩与排错都要用**）。

**`class IntentRouter(Protocol)`　L63-L66** —— 路由接口，只有一个 `classify(self, text) -> IntentResult`（L66）。换成 BERT 实现时业务代码不用改。

**`class RuleIntentRouter`　L69-L109**
- **`classify(self, text) -> IntentResult`　L76-L109**，判定顺序**是有讲究的**（L70-L75 docstring：FAQ 命中是唯一能秒回的，优先判它可省一次 LLM 调用）：
  1. L77-L79 空输入 → `CHITCHAT, 0.0`
  2. L81-L91 查 FAQ top1，**L84 必须比 `confidence` 而不是 `score`** —— L82-L83 的注释解释了原因：`score` 取 k=1 时恒为 1.0，任何查询都会被判成命中。命中则返回 `FAQ` + 详细 `reason`（含 bm25/cosine 分量）
  3. L93-L102 命中技术词 → `PROFESSIONAL`，**L97 置信度 `min(0.5 + 0.15·n, 0.9)`**，封顶 0.9 因为「词表判定不该给出绝对确定」（L96 注释）
  4. L104-L106 命中寒暄词 → `CHITCHAT, 0.7`
  5. **L108-L109 兜底** → `CHITCHAT, 0.3`，**故意给低置信度**，让 `support.py` 的兜底机制有机会转人工

**`default_router`　L112** —— `RuleIntentRouter()` 实例，类型标注为 `IntentRouter`。

---

### 📄 文件名：`support.py`（202 行）

- **文件职责**：智能客服**三层编排**的总入口。任何一层出问题都退到「转人工」，**不抛异常给用户**（S4-02-4）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `LOW_CONFIDENCE` | L33 | `0.35` | 低于它就转人工 |
| `ESCALATE_KEYWORDS` | L36 | 5 个词：人工 / 转人工 / 投诉 / 举报 / 客服在吗 | 命中直接转人工 |
| `RAG_TOP_K` | L38 | `3` | 塞进 prompt 的原文片段数 |
| `RAG_SNIPPET_CHARS` | L39 | `600` | 每篇截多长，控制 prompt 体积与费用 |
| `CHITCHAT_SYSTEM` | L41-L45 | — | 闲聊提示词，含「不要编造平台没有的服务」 |
| `RAG_SYSTEM` | L47-L51 | — | RAG 提示词，**「只依据下面提供的材料作答」**，材料没有的要说「资料中没有提到」 |

#### 核心类/函数

**`@dataclass(frozen=True) class SupportReply`　L55-L62** —— 7 个字段：`answer` / `intent` / `confidence` / `source`（`faq`/`llm`/`rag`/`human`）/ `escalated` / `reason` / `references`（RAG 引用的文章标题）。

**`_escalate(question, reason, intent, confidence) -> SupportReply`　L65-L73** —— 统一的转人工返回，`source="human"`、`escalated=True`。

**`async _retrieve_articles(db, question) -> list[tuple[str,str]] | None`　L76-L117**
- 返回 `[(标题, 正文片段)]`。**三态返回是本函数的设计要点**（L81-L82）：
  - `None` = **知识库不可用**（表不存在 / 连不上）
  - `[]` = 查得到但**没有相关内容**
  - 两者的兜底理由不同，排错时必须能分辨
- **L92-L95 是关键 try-except**：捕获 `SQLAlchemyError` 返回 `None`。L84-L88 的注释说明了为什么必须捕获：`sys_article` 是 S4-01 才加的表，旧库上压根不存在，直接查会抛 `UndefinedTableError` 变成 500；而**两个测试套件都发现不了**（fixture 的 `create_all` 总会把表建出来），只有真起服务连真库才暴露
- L96-L97 无数据返回 `[]`；L98-L102 建索引、分词、空查询返回 `[]`
- L104-L112 复用 `_Index` 的两路分量做融合（权重 0.6/0.4 与 FAQ 一致）
- L113-L117 取 top-`RAG_TOP_K`，每篇正文截到 `RAG_SNIPPET_CHARS`

**`async answer(question, db, llm=default_llm, router=default_router) -> SupportReply`　L120-L202**
- L127-L129 空提问 → 转人工
- **兜底 1（L131-L134）**：命中 `ESCALATE_KEYWORDS` → 转人工，置信度给 1.0
- **第二层（L136-L137）**：调 `router.classify(text)`
- **兜底 2（L139-L146）**：`confidence < LOW_CONFIDENCE` → 转人工，`reason` 里带上具体数值
- **第三层分支（L148-L202）**：
  - **FAQ（L149-L162）**：L150 **局部导入** `search` 避免模块级循环；L152 取 top1；L153-L161 命中则返回 `source="faq"`，`references` 带上标准问法；**L162 路由判为 FAQ 但检索无命中 → 转人工**
  - **闲聊（L164-L176）**：L165-L169 调 LLM，**`LLMError` 转人工而不是让用户看到 502**；成功则 `source="llm"`
  - **专业问题 → RAG（L178-L202）**：L179 检索；**L180-L184 `refs is None` → 「知识库不可用」**；**L185-L189 `not refs` → 「无相关内容」**（两条兜底理由不同）；L190 拼材料；L191-L194 调 LLM，失败转人工；L195-L201 成功返回 `source="rag"`，`references` 带上文章标题

---

### 📄 文件名：`politeness.py`（183 行）

- **文件职责**：爬虫礼貌性约束（TD-133）—— robots.txt 判定 + 按域抓取间隔 + 全局并发上限。模块 docstring L3-L7 说明了**为什么这三件事必须一起做**：只加 robots 不限速是「打了招呼然后使劲刷」，只限速不读 robots 是「慢慢刷人家明确不让抓的东西」。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `ROBOTS_TTL` | L37 | `3600.0` | robots.txt 缓存 1 小时（通行做法） |
| `DEFAULT_MIN_INTERVAL` | L39 | `2.0` | 目标站没写 `Crawl-delay` 时的默认间隔 |
| `MAX_CONCURRENCY` | L41 | `4` | 全局同时在飞的请求上限，防「一百个域名各抓一篇」打满带宽 |
| `ROBOTS_TIMEOUT` | L43 | `10.0` | robots.txt 的短超时 |
| `ROBOTS_MAX_BYTES` | L44 | `512_000` | robots.txt 体积上限 |

#### 核心类/函数

**`class RobotsDisallowed(Exception)`　L47-L48** —— **独立于 `CrawlError` 的异常类型**，漏接就会变成 500。

**`@dataclass class _DomainState`　L52-L58** —— 每个域一份：`allowed_all` / `parser` / `crawl_delay` / `fetched_at` / `last_request` / `lock`（`asyncio.Lock`）。

**`_states`（L61）/ `_semaphore`（L62）** —— 按 `(scheme, netloc)` 缓存。**刻意不做 LRU 淘汰**：冷启动只涉及少数几个站，进程重启就清空（L59-L60 注释）。

**`_origin(url)`　L67-L69** / **`_robots_url(url)`　L72-L74** —— 取源站二元组 / 拼 robots.txt 地址。

**`_get_semaphore()`　L77-L83** —— **懒创建**。L78-L79 说明了原因：`asyncio.Semaphore` 绑定创建时的事件循环，而 pytest-asyncio 每个用例开新循环，模块级创建会跨循环复用而报错。

**`reset_cache()`　L86-L90** —— 清空缓存与并发闸，测试用。

**`async _load_robots(url, user_agent, fetch_text) -> _DomainState`　L93-L146**
- L96-L99 **快路径**：缓存未过期直接返回（不进锁）
- L101-L103 首次见到该域则建状态并登记
- L105 进锁，**L107-L108 双重检查**：等锁期间可能已有别的协程加载完
- L109-L111 重置三个字段
- **L114 延迟导入** `CrawlError` —— 断开 `crawler` ⇄ `politeness` 的循环依赖（L112-L113 注释）
- **L116-L128 是关键 try-except**：
  - **L118-L122 `except CrawlError: raise`** —— SSRF 校验失败**必须原样抛出**，不能被下面的兜底吞成 `RobotsDisallowed`，否则「这个地址不许访问」会被伪装成「robots 不让抓」，安全告警就丢了
  - L123-L128 其它异常（超时/连接失败/DNS）→ 规则不可知，保守处理**本次不抓**
- **L130-L144 五档状态码判定**：
  - L130-L131 `401/403` → 全站禁止
  - L132-L133 `404/410` → 无限制（没有 robots 文件是绝大多数站点的常态）
  - L134-L142 `2xx` → 解析规则；**L141 必须传真实 UA 不能传 `None`** —— `Entry.applies_to` 会对 useragent 调 `.split("/")`，robots 里没有 `User-agent: *` 条目时会 `AttributeError`（L138-L140 注释）
  - L143-L144 其它（`5xx`/`3xx`）→ 不可知，不抓
- L145-L146 记时间戳并返回

**`async check_allowed(url, user_agent, fetch_text) -> None`　L149-L155** —— 不允许就抛 `RobotsDisallowed`。L152-L153 全站禁止；L154-L155 规则明确禁止该 URL。

**`min_interval_for(state) -> float`　L158-L162** —— 优先听目标站的 `Crawl-delay`，否则用默认值。

**`async throttle(url, state) -> None`　L165-L179**
- **L172-L177 必须在锁内更新 `last_request`**。L175-L176 的注释说明了后果：不在锁内记账，并发的两个协程会算出同一个等待时长、然后同时醒来一起发出去，限速就白做了
- L177 **先把「预定发出时刻」记下来再睡**，这样排在后面的协程会往后顺延
- L178-L179 需要等才睡

**`state_for(url)`　L182-L183** —— `setdefault` 取或建该域状态。

---

### 📄 文件名：`crawler.py`（190 行）

- **文件职责**：底层爬虫 —— `httpx` 抓页面 + `BeautifulSoup` 解析 + **SSRF 闸门** + DOM 骨架压缩。模块 docstring L3-L19 写了两条设计约束与一个递归陷阱。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `TIMEOUT` | L35 | `15.0` | 请求超时（秒） |
| `MAX_BYTES` | L36 | `2_000_000` | **单页内存预算**。配合 `MAX_CONCURRENCY=4` 决定最坏占用约 8 MB |
| `MAX_NODES` | L37 | `400` | 骨架最多多少节点 |
| `MAX_TEXT` | L38 | `80` | 每个文本节点截断到多少字符 |
| `USER_AGENT` | L40 | `codemax-platform/1.0 (...)` | **L39 注释是一条踩坑记录**：HTTP 头只能 latin-1 编码，UA 里写中文会在发请求时抛 `UnicodeEncodeError` |
| `DROP_TAGS` | L43-L55 | 11 个标签 | 对「识别文章结构」没帮助，先丢掉：`script` `style` `noscript` `template` `svg` `iframe` `nav` `footer` `header` `form` `aside` |

#### 核心类/函数

**`class CrawlError(Exception)`　L58-L59** —— 目标不合法或抓取失败。

**`@dataclass(frozen=True) class Page`　L63-L68** —— `url`（**跟随重定向之后的最终地址**）/ `status` / `html`。

**`async assert_public_url(url) -> None`　L71-L91**（SSRF 闸门）
- L77-L79 只允许 `http`/`https`，否则抛错（挡 `file://` `ftp://` `gopher://`）
- L80-L82 没有主机名就抛错
- L83 推端口（https→443，否则 80）
- **L84-L87 真的去解析域名**，`gaierror` 转成 `CrawlError`
- **L88-L91 逐个检查每一个解析结果**，`is_global` 一次覆盖私网/环回/链路本地/组播/保留段（含云厂商元数据地址 `169.254.169.254`）。**不能只看第一个** —— 域名可能解析出多个地址
- **校验必须在「解析之后、请求之前」**：只校验用户填的字符串没用，`http://innocent.com` 可能解析到 `10.0.0.5`

**`async _request(url, *, transport, max_bytes) -> httpx.Response`　L94-L112**
- **只做 SSRF 校验 + 发请求，不做礼貌性检查**。L96-L99 说明这一层的存在理由：robots.txt 自己的抓取也要防 SSRF，但**绝不能再触发一次 `check_allowed`**，否则就是「为了判断能不能抓 robots.txt 而去读 robots.txt」的无限递归
- L101 校验 → L102-L109 发请求（`follow_redirects=True`、带 UA）→ L110-L111 超体积抛错

**`async fetch(url, *, transport=None, max_bytes=MAX_BYTES) -> Page`　L115-L138**
- L125-L129 定义注入给 politeness 的 `fetch_text`（复用 `_request`，因此 robots 抓取也过 SSRF）
- **L131 先 robots** → **L134 再限速**。L121-L122 说明顺序原因：反过来会为了一个根本不让抓的 URL 白等一个抓取间隔
- L133 进全局并发闸 → L135 发请求
- L136-L137 非 200 抛错；L138 返回 `Page`，**`url` 取 `str(r.url)`（重定向后的最终地址）**

**`to_skeleton(html, *, max_nodes=MAX_NODES, max_text=MAX_TEXT) -> str`　L141-L190**
- L163-L165 解析 HTML 并 `decompose()` 掉 11 类噪声标签
- **L169-L187 内部递归函数 `walk`**：
  - L171-L172 **达到 `max_nodes` 就返回**（这是输出有界的保证）
  - L173-L174 跳过注释节点
  - L175-L179 文本节点：折叠空白、截断到 `max_text`、加引号与缩进
  - L180-L186 元素节点：标签名 + `#id` + **前 3 个** class
  - L187 递归子节点
- L189 从 `soup.body`（没有就整个 soup）开始走
- **L155-L161 的 docstring 记了一条被更正过的说法**：真正的保证是**输出有界**而非压缩比 —— 实测 2.66 MB / 10000 节点的页面压出 13 198 字符、恰好 400 行；而压缩比随页面形态在 **1/1.5 ~ 1/201** 之间摆动

---

### 📄 文件名：`extract.py`（165 行）

- **文件职责**：LLM 智能解析 —— **模型指认 CSS 选择器，BeautifulSoup 负责提取**（TD-135）。模块 docstring L3-L11 说明了为什么不 let LLM 直接吐正文：它会改写、删节甚至杜撰原文，而且每次结果不一样、没法写回归测试。

#### 关键常量

| 常量 | 行 | 值 |
| --- | --- | --- |
| `FIELDS` | L31 | `("title", "author", "published_at", "content")` |
| `REQUIRED` | L32 | `("title", "content")` —— 另外两个允许缺 |
| `SYSTEM_PROMPT` | L34-L45 | 7 条输出要求：只输出一个 JSON 对象、键固定四个、值是 CSS 选择器、**只能用骨架里出现过的**标签/id/class、`content` 要指向正文容器、判断不了给空串、忽略导航与评论区 |
| `_FENCE` | L47 | ```` ```json ```` 围栏正则（含未闭合），与 `llm.py` 同套路 |

#### 核心类/函数

**`class ExtractError(RuntimeError)`　L50-L51** —— 解析失败。

**`@dataclass(frozen=True) class ParsedArticle`　L55-L63** —— 6 个字段：`url` / `source_site` / `title` / `author` / `published_at` / `content`。`published_at` **保留源站原文**（TD-137）。

**`async identify_selectors(skeleton, llm=default_llm) -> dict[str,str]`　L66-L72**
- **L68-L71 是关键 try-except**：把 `LLMError` **包成 `ExtractError` 再抛**（`raise ExtractError(str(e)) from e`）。
- ⚠️ **这个换包装有个下游后果**：`app/routers/admin.py` 里写 `except LLMError` 是**永不可达的死分支** —— 大模型故障到这里时已经是 `ExtractError` 了，必须顺着 `__cause__` 认回去。实测确认过（`ARCHITECTURE_GUIDE.md` 6.8）。

**`_parse_selectors(reply) -> dict[str,str]`　L75-L87**
- L76-L77 去围栏
- L78-L81 `json.loads` 失败 → 抛错并带原始输出前 200 字符
- L82-L83 不是 JSON 对象 → 抛错
- L84-L86 **有未知字段 → 抛错**（防止模型自作主张加键）
- L87 按 `FIELDS` 取值，`None` 归一成空串并 `strip()`

**`extract_fields(html, selectors) -> dict[str,str]`　L90-L112**
- L92 解析 HTML
- L94-L97 选择器为空 → 该字段留空并跳过
- **L98-L101 `soup.select_one` 抛异常**（soupsieve 对非法选择器）→ 转成业务错误
- **L102-L103 匹配不到节点 → 抛错**。docstring L91 说明理由：这是「该重试/该报警」的信号，**不能静默留空**
- **L104-L108 `content` 特殊处理**：按块级标签（`p`/`li`/`h2`/`h3`/`pre`）换行保留段落结构；找不到块级标签时退回整节点文本
- L109-L110 其余字段压成单行

**`async parse_page(url, html, *, llm=default_llm) -> ParsedArticle`　L115-L134**
- **L117-L120 docstring 是本函数的设计要点**：「怎么抓到这个 html 的，本函数不管」—— httpx 静态抓取与无头浏览器渲染**都调它**，所以两条路径的解析行为完全一致，不会「换个引擎结果就不一样」
- L122 骨架 → LLM 指认；L123 按选择器提取
- **L124-L126 必需字段为空就抛错**，错误信息里带上用的选择器
- L127-L134 组装结果：L129 `source_site` 从 url 取 netloc；L130-L132 三个字段分别截断到 300/100/50 字符，空串归 `None`

**`async parse_article(url, *, transport=None, llm=default_llm) -> ParsedArticle`　L137-L149**
- 静态路径：L148 `fetch` → L149 交给 `parse_page`。**传的是 `page.url`（重定向后的最终地址）**，这样入库的 `url` 与 `source_site` 才是页面真实来源（L143-L145 docstring）。

**`async save_article(db, article) -> Article`　L152-L165**
- L154 按 URL 查已有行 → L155 有则更新、无则新建 → L156-L160 覆写 5 个字段 → L161-L162 新行才 `add` → L163-L165 commit + refresh 返回。**同一 URL 重复抓是更新而不是新增。**

---

### 📄 文件名：`browser.py`（117 行）

- **文件职责**：动态页面渲染（Playwright，TD-191）—— 给 `httpx` 抓不到正文的 SPA 站点兜底。**playwright 不是 `requirements.txt` 里的必需依赖**（wheel 47 MB + 浏览器约 150 MB），没装时端点返回 503 并给出安装命令，而不是启动时炸掉整个应用（模块 docstring L8-L11）。

#### 关键常量

| 常量 | 行 | 值 | 作用 |
| --- | --- | --- | --- |
| `RENDER_TIMEOUT_MS` | L30 | `int(TIMEOUT*1000)` = 15000 | L28-L29 说明：`networkidle` 对 SPA 最有效，但有些站点一直有心跳请求导致永不 idle，所以给上限，超时就按当前 DOM 取 —— **拿到半页也比拿不到强** |

#### 核心类/函数

**`class BrowserUnavailable(RuntimeError)`　L33-L34** —— 没装 playwright，或装了但没下载浏览器二进制。路由层映射成 **503**（本站能力缺失，不是调用方的错）。

**`browser_available() -> bool`　L37-L48**
- 只做便宜的 **import 探测**（L44-L47），避免为了探测而启动一次 node 驱动进程
- ⚠️ **L39-L41 明确警告：这不代表浏览器二进制已下载** —— 那要另外跑 `playwright install chromium`，二进制缺失要到真正 launch 时才暴露

**`async render(url, *, transport=None) -> Page`　L51-L81**
- **顺序是本模块可测的关键**（模块 docstring L13-L17）：前三步不碰浏览器，所以在沙箱里就能真跑真测；真正需要浏览器的只有最后的 `_goto()`
- **L61 ① SSRF 校验** —— 必须在启动浏览器**之前**，浏览器不会替你做这个判断。少了它 `dynamic=true` 就等于开了一个能访问 `169.254.169.254` 的口子
- L64-L68 **② robots 判定** —— 动态抓取也是抓取，不能因为换了引擎就绕过站方意愿
- L71-L75 **③ 限速 + 全局并发闸** → **④ 到这里才碰浏览器**
- **L79-L80 体积上限放在 `render` 而不是 `_goto`** —— L77-L78 说明理由：这样它不需要真浏览器就能被测到
- 复用 `crawler.Page` 是刻意的（L54-L55）：动态路径要带上重定向后的最终 URL

**`async _goto(url) -> Page`　L84-L117**（唯一需要浏览器二进制的一步）
- **L86-L93 第一层 try-except**：连 playwright 包都没装 → `BrowserUnavailable`，错误信息里**直接给出两条安装命令**
- L95-L104 启动 chromium、开页（带 UA）、`goto`（`wait_until="networkidle"` + 超时）、取 `content()` 与最终 URL；**L103-L104 `finally` 确保关浏览器**
- **L105-L106 `except BrowserUnavailable: raise`** —— 先放行已知异常
- **L107-L115 宽接 `Exception`**。L108-L110 的注释解释了为什么这里宽接是安全的：**SSRF 与 robots 已经在上面跑完了，没有任何安全检查在这个 try 里**，所以不存在「把安全错误洗成业务错误」的问题。最常见原因是浏览器二进制没下载，报错里直接给命令而不是让人猜
- L117 返回 `Page`（`status` 固定 200）

---

## 3. 执行逻辑流

### 3.1 A 链：SQL → ER 图 → Word

```
POST /tools/er-diagram  (app/routers/tools.py)
  └─ sql_ddl.parse_ddl(sql)                        L39-L49
       ├─ _strip_comments()                        L99   ← 走 _scan，认得字符串与注释
       ├─ _iter_tables()                           L109  ← 跳过字符串里的 CREATE TABLE
       │    ├─ _read_balanced()                    L124  ← 忽略字符串内的括号
       │    └─ _parse_table()                      L157
       │         ├─ _split_top_level()             L139  ← 只切顶层逗号
       │         ├─ _parse_column()                L181  ← 列 + 行内 REFERENCES 边
       │         └─ _parse_constraint()            L219  ← 表级 PK / FK
       └─ _apply_comments()                        L242  ← COMMENT ON TABLE/COLUMN
  └─ 返回 {tables, edges} 给前端 D3.js 渲染

POST /tools/word-export
  └─ sql_ddl.parse_ddl(sql)  →  word.build_data_dictionary(graph)   L18-L51
       └─ 返回 .docx 字节 + MIME_DOCX + FILENAME

POST /tools/mermaid
  └─ llm.generate_mermaid(text)                    L93-L99
       ├─ llm.LLMClient.chat()                     L54-L78   ← 可注入 transport
       ├─ _strip_fence()                           L102
       └─ 校验首关键字在 _DIAGRAM_TYPES 里          L96-L97
```

**数据不在 A 链的文件之间流转**（`sql_ddl` 与 `word` 互不 import），是**路由层把 `parse_ddl` 的输出喂给 `build_data_dictionary`** —— 这正是「tools 是纯函数、routers 做编排」的体现。

### 3.2 B 链：一次客服提问

```
POST /support/ask  (app/routers/support.py:12)
  └─ support.answer(question, db, llm, router)      L120-L202
       │
       ├─ L127-L129  空提问 ────────────────────────▶ 转人工
       ├─ L131-L134  命中 ESCALATE_KEYWORDS ────────▶ 转人工（兜底 1）
       │
       ├─ L137  intent.RuleIntentRouter.classify()   L76-L109
       │         ├─ L81-L91  faq.search(q, k=1)  ──▶ faq.py L174-L198
       │         │              └─ _INDEX.bm25() / .cosine()   L111 / L124
       │         │           比 confidence ≥ 0.40 → FAQ
       │         ├─ L92-L99  命中 31 个技术词 → PROFESSIONAL
       │         ├─ L101-L103 命中 9 个寒暄词 → CHITCHAT
       │         └─ L106     兜底 → CHITCHAT, 0.3（故意低，好让上层转人工）
       │
       ├─ L139-L146  confidence < 0.35 ─────────────▶ 转人工（兜底 2）
       │
       └─ L148-L202  三分支：
            ├─ FAQ          L152  faq.search(k=1) ──▶ 秒回，不调 LLM
            ├─ CHITCHAT     L166  llm.chat(CHITCHAT_SYSTEM)  失败→转人工
            └─ PROFESSIONAL L179  _retrieve_articles()  L76-L117
                                   ├─ None → 「知识库不可用」转人工
                                   ├─ []   → 「无相关内容」转人工
                                   └─ 有   → L192 llm.chat(RAG_SYSTEM, 材料+问题)
                                             失败→转人工；成功→带 references
```

**关键点：三个分支的失败出口全部收敛到 `_escalate()`，没有一条路径会把异常抛给用户。**

### 3.3 C 链：一次内容抓取

```
POST /admin/articles/ingest  (app/routers/admin.py)
  │
  ├─ dynamic=false ─▶ extract.parse_article(url)          L137-L149
  │                     └─ crawler.fetch(url)             L115-L138
  │
  └─ dynamic=true  ─▶ browser.render(url)                 L51-L81
                        │  ① crawler.assert_public_url    L61 → crawler L71-L91
                        │  ② politeness.check_allowed     L68 → politeness L149-L155
                        │  ③ politeness._get_semaphore    L72
                        │  ④ politeness.throttle          L73 → politeness L165-L179
                        │  ⑤ _goto(url)  ← 唯一需要浏览器  L75 → L84-L117
                        └─ 两条路径都得到 crawler.Page
  │
  ▼
extract.parse_page(page.url, page.html)                   L115-L134
  ├─ crawler.to_skeleton(html)                            L141-L190
  ├─ identify_selectors(skeleton)                         L66-L72
  │    └─ llm.LLMClient.chat(SYSTEM_PROMPT, skeleton)
  │       （LLMError 在此被包成 ExtractError，L68-L71）
  ├─ _parse_selectors(reply)                              L75-L87
  ├─ extract_fields(html, selectors)                      L90-L112
  └─ 必需字段校验 + 组装 ParsedArticle                     L124-L134
  │
  ▼
extract.save_article(db, parsed)                          L152-L165
  └─ 同 URL 已存在则更新，否则新增 → sys_article 表
```

**`crawler.fetch` 内部的顺序（L131-L135）**：

```
fetch(url)
  ├─ politeness.check_allowed(url, UA, fetch_text)   L131  ← 先 robots
  │    └─ _load_robots()                             politeness L93-L146
  │         └─ fetch_text() → crawler._request()     L94-L112
  │              └─ assert_public_url()  ← robots 自己也过 SSRF，但不再过 check_allowed
  ├─ politeness._get_semaphore()                     L133  ← 全局并发闸（4）
  ├─ politeness.throttle(url, state)                 L134  ← 再限速（按域）
  └─ crawler._request(url)                           L135  ← 最后才抓正文
```

**为什么 `_request` 与 `fetch` 要拆两层**：robots.txt 自己的抓取必须过 SSRF 校验，但绝不能再过 `check_allowed` —— 否则就是「为了判断能不能抓 robots.txt 而去读 robots.txt」的无限递归（`crawler.py` L16-L19）。

---

## 附：行号与数字怎么复核

本文的行号与统计数字都对应 commit `3501db8`。复核命令（在仓库根目录、已激活 `.venv`）：

```bash
# 1) 每个文件的类/函数精确行范围（本文所有 Lxx-Lyy 的来源）
python -c "import ast,pathlib;[print(f'{n.lineno}-{n.end_lineno} {getattr(n,\"name\",\"\")}') for p in sorted(pathlib.Path('app/tools').glob('*.py')) for n in ast.walk(ast.parse(p.read_text(encoding='utf-8'))) if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef))]"

# 2) 本文引用的常量与计数
python -c "from app.tools.faq import FAQS,BM25_WEIGHT; from app.tools.intent import FAQ_CONFIDENCE_THRESHOLD as T,PROFESSIONAL_KEYWORDS as P,CHITCHAT_KEYWORDS as C; from app.tools import support as s; print(f'FAQS={len(FAQS)} BM25_WEIGHT={BM25_WEIGHT} 阈值={T} 技术词={len(P)} 寒暄词={len(C)} LOW_CONFIDENCE={s.LOW_CONFIDENCE} RAG_TOP_K={s.RAG_TOP_K}')"
# 预期：FAQS=12 BM25_WEIGHT=0.6 阈值=0.4 技术词=31 寒暄词=9 LOW_CONFIDENCE=0.35 RAG_TOP_K=3

# 3) 依赖关系
grep -n "^from \.\|^from \.\.\|    from \." app/tools/*.py

# 4) 本模块的测试（注意文件名与模块名不是一一对应）
#    word.py → test_word_export.py；llm.py → test_mermaid.py；intent.py 由 test_support.py 覆盖
python -m pytest tests/test_sql_ddl.py tests/test_word_export.py tests/test_mermaid.py tests/test_faq.py tests/test_support.py tests/test_crawler.py tests/test_politeness.py tests/test_extract.py tests/test_dynamic_crawl.py -q
# 本机实测：171 passed, 1 skipped（那条 skip 是 TD-191 的真浏览器用例，沙箱下不到浏览器二进制）
```

> **行号会腐烂。** 按 `AGENTS.md` 的 ALWAYS 段与 TD-195，改动 `app/tools/` 下任何文件后，本文对应的行号与计数**必须同步更新**，并把文首的「行号基准 commit」改成新的 SHA。
>
> 更深的背景（为什么文档里的数字比代码更容易腐烂、以及一次真实的漏改事故）见 `docs/ARCHITECTURE_GUIDE.md` 第 7 课 7.10。
