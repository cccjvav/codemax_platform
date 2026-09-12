# 编码与文档维护约定

本会话固定分支 `arena/01a08bf5-codemax-platform`。当前架构见 docs/ARCHITECTURE_GUIDE.md，交接见 HANDOVER.md，内容审核见 docs/DOCUMENTATION_QUALITY_REVIEW.md；历史报告或 Skill 中旧章节号/固定测试数不覆盖当前指南。

## 硬边界

- 不切换/创建/推送其他分支；未经用户明确授权不合并 PR。
- 不以删除测试、降低断言、增加无理由 skip 或关闭扫描掩盖失败。
- 不提交真实 .env、密钥、测试数据、环境目录、临时日志或未请求的演示。鹈鹕及相关文档扩写继续排除。
- 保持 Python 后端；Node 用于 Vite 构建与前端测试，不引入生产 Node/Java 服务。
- 不能把未验证的推断写成事实；区分纯函数、HTTP 集成、真实 PostgreSQL、Node VM、浏览器和外部服务联调。
- 用户已批准的本轮迁移、重登录、下载恢复、站内客服与文档重做不反复询问；新的依赖、schema、业务范围或生产操作先确认。

## 工作循环

1. 阅读当前源码、实际调用方与对应 README，不依赖历史行号或凭模块名猜测试文件。
2. 修复配套回归测试；关键边界执行有控制的反例/变异检查，恢复原实现后再验。无法验证的范围明确记录。
3. 运行 Ruff、相关测试、全量测试；数据库并发和迁移另外跑真实 PostgreSQL。只用可丢弃测试库，fixture 会重建表。
4. 更新人工解释，再刷新 README 指纹。不得先刷新生成表而不检查正文语义。
5. 功能变更同步架构指南；按“大白话、生活比喻、落到代码、术语”说明。已撤销行为改正文，不仅在文末追加矛盾结论。
6. 新取舍在 TECH_DECISIONS 追加有编号的决定、代价与回看条件；更新 HANDOVER/ROADMAP 当前入口，不改写历史测试证据来凑新数字。
7. 审查 git diff 与状态后选择性提交；用 `git commit -F`。随即推送本固定分支并用 git ls-remote 核对 tip；认证失败在 Arena 重连，不索要密码/令牌。
8. 查询最终 SHA 的每个 CI job。上一提交的成功、运行中或 cancelled 都不是本次通过；日志取不到应明确说明并另行获取证据。

## 常用验证

以下为 Linux 沙箱命令；给用户 Windows 本机操作时使用 docs/WINDOWS_LOCAL_RUN.md 的 cmd 语法，不混用 shell。

```bash
.venv/bin/ruff check .
.venv/bin/python -m pytest -q
.venv/bin/python scripts/check_docs_contract.py
.venv/bin/python scripts/build_docs_site.py
npm run build
```

真实 PostgreSQL 配置 TEST_DATABASE_URL 后运行同样 pytest 命令。不得指向业务库；不要将生产凭据放到聊天或报告。
修改 app/frontend 后提交所有相关 app/static/js 分块；不手改第三方生成字符串来掩盖格式问题。普通文档构建不需要数据库或模型 key。

## 结构与文档规则

- 模型变化同步 models.py、full_init.sql 与必要增量迁移。full_init 会删表，不用于存量升级。
- 优先把可复用算法放 tools/公共层；如路由实际包含查询和事务，文档必须如实解释，而非声称已经完全分层。
- 配置集中 Settings；db_init 脚本的独立 DB 环境读取和 Compose 变量是现有例外，不推广散落的 getenv。
- 目录 README 必须解释职责、入口、数据流/约束、变更/验证；函数应交代输入输出、权限、写入、失败与调用要求。可分组解释简单 helper，不复制函数体凑字数。
- 源码位置来自 AST/生成页；新文件、乱码、空壳、过期指纹和坏链接有门禁；语义正确性仍需源码核对。
- 数量和测试结果以本次生成/运行记录为准，不在多个现行文档里手写相同易漂数字。引用测试路径先确认文件存在；扫描器要有正/反例。
- `.github/workflows/ci.yml` 是全局门禁，变更前说明影响，变更后等待实际 CI；不要仅更新 README 就声称更改了工作流。

## 面向新手的代码复盘

用户需要理解功能协作、函数和关键语句，不要求逐句认证历史报告。契约表/AST/docstring 不是逐行教程；补精读时读源码/调用者/测试，把导入、字段、条件、异常、副作用和修改影响讲清楚。人工数据在 docs/code_reading_notes.json，具体规范见 docs/CODE_READING_GUIDE.md。
源码旁的讲解必须绑定当前 SHA、连续段界并覆盖文件尾；不得只更新摘要表绕过陈旧精读。覆盖表必须展示待补文件；不得把“所有文件可定位”说成“全部语义已认证”。不要将生成第三方库逐行重述充当业务源码讲解。

## 当前特别边界

动态 Chromium 停用；安装检测不代表可用。只有 LocalStorage 实现；真实商户、云适配器、通知、发票等未验收事项按部署/质量报告说明。
默认单实例；数据库锁不等于共享限流。网站账号角色是后端权限，不由前端控件或 JWT 自报决定。

按需读取 .claude/skills：codemax-workflow、fastapi-python、python-testing、schema-sync、new-tool-page、pre-commit-review、finish-subitem；旧示例数字不作为现行验收标准。
