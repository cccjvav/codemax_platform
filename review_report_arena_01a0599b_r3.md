# 复审报告（第 3 轮）

**审查目标**  
`origin/arena/01a0599b-codemax-platform@219ce332a83959b38880e0f17ae346f86d4a1ae7`

**审查方式**  
本轮为**只读分析**，未修改目标分支代码。重点复核了：

- 实现正确性
- 回归风险
- 测试覆盖与真实 PostgreSQL 表现
- 文档与自动统计是否同步
- 商业页 / 登录 / 支付相关前后端联动

---

## 一、总体结论

这次分支**没有我上一轮一度怀疑的 PostgreSQL 硬回归**；在全新 PG 数据目录与全新数据库上，全量真实 PostgreSQL 测试已跑通。

但分支仍然**不算完全收尾**，当前至少还有以下 3 类已确认问题：

1. **商城页登录后重试逻辑仍存在监听器泄漏 / 幽灵下单问题**
2. **`/shop/mock-pay` 页面缺少 `base.html` 所需上下文，导致头部 / CTA 渲染为空**
3. **大量手写文档数字已与当前代码 / 测试 / 路由现状脱节**

因此最终判断是：

- **后端主链路和真实 PG 状态：比上一轮判断更健康**
- **前端商业页与文档同步：仍有明确缺陷**
- **不建议以“本轮已全部修完”的状态结束这项工作**

---

## 二、已完成验证

### 1. 静态检查
- `ruff check .`：**通过**

### 2. 文档站构建
- `python scripts/build_docs_site.py --data-only`：**通过**
  - `77 modules`
  - `198 edges`
  - `39 routes`
  - `265 symbols`
  - `21 docs`
  - `9964 doc lines`
- `python scripts/build_docs_site.py`：**通过**

### 3. 测试结果
- SQLite 全量：**`526 passed, 4 skipped, 4 warnings`**
- fresh PostgreSQL 全量：**`528 passed, 2 skipped, 2 warnings`**

### 4. 当前实测口径
- 代码文件：`99`（不含 `app/static/pay_qr.svg`）/ `15594` 行
- 文档：`21` 份 / `9964` 行
- 测试：
  - `37` 个测试 `.py`
  - 其中 `35` 个是 `test_*.py`
  - `473` 个测试函数
- 路由：
  - `39` 个业务 method+path 条目
  - `34` 个唯一路径
- settings 字段：`40`

---

## 三、需要纠正的上一轮结论

## PostgreSQL “大回归”目前不成立

上一轮曾拿到一组很差的 real-PG 结果：

- `1 failed, 456 passed, 2 skipped, 71 errors`

当时错误表现包括：
- `oauth_client(client_id)` 重复插入
- 后续大量 `sys_user` 缺表

但这次在**全新 PG 数据目录**和**全新数据库**上完整重跑后，结果是：

- **`528 passed, 2 skipped, 2 warnings`**

因此更合理的结论应为：

> 之前那次异常更像是旧 PG 环境 / 脏库态导致的假警报，当前没有证据证明该分支存在稳定可复现的 PostgreSQL 级回归。

建议在最终反馈里**明确撤回 / 降级**这项指控，避免误导后续判断。

---

# 四、已确认问题

## 问题 1：商城页 `buy()` 的登录后重试监听器仍然泄漏
**级别：高于一般前端小问题，建议列为主要缺陷**

### 证据位置
- `app/templates/shop.html:192-201`
- `app/templates/shop.html:244`
- `app/static/auth.js:30,40,119`

### 现象
`shop.html` 的 `buy()` 在下单返回 401 时，会：

- 打开登录浮层：`CodeMaxAuth.open("login")`
- 注册一个 `CodeMaxAuth.onChange(once)` 监听器
- 期望登录成功后自动继续 `buy()`

但 `auth.js` 里的 `CodeMaxAuth.onChange` 实现是：

- 只有 `listeners.push(f)`
- **没有退订 / 移除接口**
- `notify()` 会每次遍历所有历史监听器

同时，`shop.html` 中这句：

```js
CodeMaxAuth.onChange(() => {});
```

并不会移除旧监听器，反而只是**再追加一个空监听器**。

### 实际复现结果
我用 Node 把 `auth.js` 和 `shop.html` 的前端脚本真实执行了一遍，模拟流程如下：

1. 未登录点击“立即购买”
2. `/shop/orders` 返回 401
3. 登录成功
4. 自动补发一次 `buy()` —— 这是预期行为
5. 随后退出登录，再重新登录一次，**即便不再点击购买**
6. 旧监听器再次触发，`buy()` 又被自动调用一次

复现结果显示：
- 第一次登录后：下单调用次数 = `1`
- 第二次只是重新登录后：下单调用次数 = `2`

也就是说，旧的购买意图会残留到后续无关登录流程里。

### 风险
这不是单纯的内存泄漏，而是**业务动作被重复重放**：

- 多次未登录点击会积累多个监听器
- 下次成功登录时可能触发多次 `buy()`
- 后续再次登录时，历史监听器还可能继续触发
- 用户可能在自己并未再次点击购买的情况下，被自动重放下单动作

### 为什么后端兜底不够
后端当前的“同一用户最多一张 pending 单”约束只能**减损**，不能修复：

- 挡不住多余请求
- 挡不住错误的购买意图重放
- 如果当时没有 pending 单，仍可能生成新订单

### 为什么测试没抓住
当前测试覆盖了：
- `auth.js` 是否可访问
- drawio 页前端不触碰 `localStorage`
- shop 的服务端 API 与 HTML

但**没有真实执行 `shop.html` 里“401 -> 登录 -> 自动重试 -> 再登录”这条脚本路径**，所以这个 bug 还漏着。

### 结论
这是当前分支里最实质性的残留问题之一，优先级应高于文档漂移。

---

## 问题 2：`/shop/mock-pay` 页面模板上下文不完整，导致 base 头部空渲染
**级别：中**

### 证据位置
- `app/routers/shop.py:205-217`
- `app/templates/base.html:57-61`
- `app/templates/base.html:103`

### 原因
`mock_pay_page()` 返回模板时只传了：

- `request`
- `title`
- `order_no`
- `auth_ui`

但 `base.html` 依赖更多公共字段：

- `site_name`
- `tools`
- `shop_path`
- `product_name`
- 常规 SEO 字段如 `description` / `keywords` / `canonical`

普通页面这些字段由 `app/routers/site.py` 的 `_page_view()` 统一补齐；`mock_pay_page()` 没复用这套上下文。

### 实测渲染结果
请求 `GET /shop/mock-pay?order_no=CM1` 时，页面虽然返回 200，但 header 会渲染成空壳：

- `<h1><a href="/"></a></h1>`
- `<nav></nav>`
- 商城 CTA 的 `href=""`
- 按钮文字为空

### 影响
该问题发生在支付链路页，影响并不低：

- 头部品牌信息为空
- 导航缺失
- CTA 链接无效
- 页面专业感 / 可信度下降
- 也说明模板上下文契约没有统一收口

### 为什么测试没抓住
`tests/test_mock_pay.py` 当前只验证：
- 页面能开
- 有“模拟支付通道”提示
- 有订单号

但没有验证：
- `base.html` header 是否完整
- 全局入口是否正常
- CTA 是否有文案和正确链接

### 结论
这是一个明确的页面实现缺陷，不是单纯样式问题。

---

## 问题 3：文档与说明书大面积数值漂移
**级别：中，范围广**

自动生成统计已更新，但大量手写文档仍停留在旧数字。

### 建议统一采用的当前基线
- 代码文件：`99`（不含 `pay_qr.svg`）/ `15594` 行
- 文档：`21` 份 / `9964` 行
- 测试：`37` 个测试 `.py`，其中 `35` 个 `test_*.py`，`473` 个测试函数
- 路由：`39` 个业务 method+path 条目，`34` 个唯一路径
- settings 字段：`40`

### 已确认存在漂移的文件

#### `README.md`
仍写着：
- `31 条业务路由`
- `21 份文档 / 约 9 500 行`
- `tests/README.md` 是 `30 个文件 / 389 个用例`

#### `HANDOVER.md`
仍残留：
- `tests/ # 9 个测试文件，74 用例`
- `pytest -q # 209 个：SQLite 上 208 绿 + 1 跳过`

#### `TECH_DECISIONS.md`
TD-80 相关位置仍写旧口径：
- `418 + 2 skipped / 419 + 1 skipped`

#### `DOCUMENTATION_SUMMARY.md`
仍写：
- `build_docs_site.py` 为 `898 行`
- `tests/README.md` 是 `30 个文件 / 389 个用例`

#### `docs/ARCHITECTURE_GUIDE.md`
仍残留：
- `417`
- `418`
- `419`

#### `tests/README.md`
仍保留：
- `30 个 test_*.py`
- `389 个测试`
- `32 个测试文件`
- 旧的 `418 / 419` 通过数
- 复核命令中的 `6397` 行 / `389` 测试函数预期值

#### `scripts/README.md`
仍写：
- `build_docs_site.py` 为 `898 行`

当前实测为：
- **935 行**

#### `.github/workflows/README.md`
仍写：
- `ci.yml` 为 `181 行`

当前实测为：
- **229 行**

#### `总览.md`
仍写：
- `32 个 py`
- `389 个用例`
- `.github/workflows/README.md` 对应 `181 行`

#### `app/templates/README.md`
仍写：
- 内联 `<script>` 只存在于 `er / mermaid / drawio / mock_pay` 四个模板中

但当前 `shop.html` 也已有内联脚本。

### 这类问题为什么不能轻视
这不是单点 typo，而是**全仓多份“项目画像”同时失真**：

- README 说一种规模
- HANDOVER 说另一种规模
- `tests/README.md` 再说第三种规模
- 自动构建数据又是第四种口径

这会直接影响：
- 接手判断
- 评审可信度
- “文档是否和实现同步”的总体印象

### 结论
文档同步工作这轮明显还没收口，不能说“文档已跟上实现”。

---

# 五、其他值得保留的结论

## 1. `app/static/pay_qr.svg` 仍是明确占位图
- 这是**上线前待办**
- 不是隐藏 bug
- manual 模式若要用于真实演示 / 生产，必须替换成真实收款码

文件本身对此写得很清楚，因此应归类为**产品 / 部署准备项**，不是代码 defect。

## 2. 后端主链路本轮未再发现新的 correctness 破坏
复查了：
- `app/routers/shop.py`
- `database init/full_init.sql`
- `app/tools/faq.py`
- `app/tools/intent.py`
- `app/tools/llm.py`

结合 fresh PG 全绿，本轮**没有新增已确认后端逻辑破坏**。尤其是：

- `full_init.sql` 中 admin `role=1` 的问题已修到位
- manual / mock / wechat 三种支付模式后端逻辑基本自洽
- 一次性下载 / 状态机 / 订单轮询主线没有再发现新的硬伤

---

# 六、最终建议

## 是否建议直接判定“已修完”？
**不建议。**

## 是否还存在“不能合并的后端硬阻塞”？
**目前没有证据支持这一点。** 之前怀疑的 PG 回归应撤回。

## 当前更准确的评价
建议对外表述为：

> 该分支的后端稳定性、测试、真实 PostgreSQL 表现整体已明显好于上一轮怀疑；但商城前端登录后重试仍有真实业务 bug，mock-pay 页面的模板壳仍未收齐，文档统计也有明显漂移，因此本轮不能视为完全收尾。

---

# 七、建议对外反馈的优先级排序

### P1
1. **修复 `shop.html` / `auth.js` 的监听器泄漏与重复 buy 问题**

### P2
2. **修复 `/shop/mock-pay` 的模板上下文字段缺失**
   - 至少补齐 `base.html` 所需公共字段

### P2
3. **统一更新文档数字**
   - 重点先修：`README.md`、`HANDOVER.md`、`tests/README.md`、`TECH_DECISIONS.md`、`.github/workflows/README.md`、`scripts/README.md`、`总览.md`

### P3
4. **补前端行为测试**
   - 增加一条能真实执行 `shop.html` 登录重试脚本的测试
   - 增加一条验证 `mock-pay` 页面 header / CTA 不为空的测试

---

# 八、附：本轮最关键的结论摘要

- **撤回项**：此前怀疑的 PostgreSQL 大回归，当前不成立
- **确认缺陷 1**：`shop.html` 登录后自动重试监听器泄漏，可导致幽灵下单 / 重复下单请求
- **确认缺陷 2**：`/shop/mock-pay` 缺少 base 模板上下文，页面头部与 CTA 空渲染
- **确认缺陷 3**：文档统计大面积过期，多个说明文件之间口径互相冲突
- **整体判断**：后端比上一轮判断更健康，但前端与文档仍未真正收尾
