# 交接与恢复工作

Windows 本机以 [conda 指南](docs/WINDOWS_CONDA.md) 为主要路径；剩余项目依 [验收手册](docs/ACCEPTANCE_GUIDE.md) 分层执行，并非全部只能在 Windows。未执行项不转写成通过记录。

## 当前入口

- 第二批实现与迁移：[验收记录](docs/SECOND_REPAIR_ACCEPTANCE.md)
- 文档语义重做、排版和 CI 修复：[质量复核](docs/DOCUMENTATION_QUALITY_REVIEW.md)
- 当前架构：[架构指南](docs/ARCHITECTURE_GUIDE.md)；执行方式：[根 README](README.md)
- 原始问题与交叉分类：[交叉审查](docs/REVIEW_CROSSCHECK.md)

旧沙箱次数、过期测试数字、一次性下载旧规则不再放在当前交接入口；历史内容保留在 Git 记录，不用来覆盖当前指南。

## 已实现与需要注意的边界

凭据版本、已购链接重领、站内客户/管理员会话、Drawio 实时导出/版本与生命周期隔离、配额预算、模型缓存、文档结构及链接门禁已落地。
动态 Chromium 明确停用，静态抓取保留。LocalStorage 是当前唯一实现；未接 OSS/COS、开票、自动退款、外部通知或自助密码恢复。单个配置商品不能冒充多 SKU 目录。

存量库先备份、停写，在已有 0006 结构上依次执行 0007/0008，部署后重新登录。full_init 会删表，不用于升级。0008 重跑仍清授权码。

## 恢复环境

1. 确认分支与 git status，读取实际未提交改动，不重跑历史临时修复脚本。
2. 使用 Python 3.11 虚拟环境安装 requirements，前端 npm ci；不要把环境与构建临时日志提交。
3. 默认全量测试用 SQLite；真实 PostgreSQL 指向专用可丢弃测试库，绝不指向业务库。
4. 阅读变更源码和 README，再刷新文档指纹、构建离线站。不是先 --write 再假称完成语义审查。
5. 交付必须绑定最终 SHA，实际查询每个 CI job。GitHub 认证失败只在 Arena 重连，不向用户索要密码或 token。

## 保持的约束

- 固定使用本 Arena 会话分支，不切换或新建分支。
- 用户上传的新旧文档提案和汇总保留原文；不要当成全部已实现的规格。
- 不上传鹈鹕演示、素材或相关文档扩写。
- 没做真实浏览器/商户/模型联调就明确说明，不用 Node/mock 结果替代。
- 以当前代码和真实测试输出为准，不根据 job 名或历史注释猜根因。
