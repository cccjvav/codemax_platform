# Windows 本地运行入口

你使用 **conda** 时，请从 [Windows + conda：运行、维护与测试](WINDOWS_CONDA.md) 开始；不需要 `.venv`。该指南覆盖解释器核对、依赖安装、PostgreSQL、新库初始化、每日启动、环境维护、SQLite/真实 PG 测试及文档构建。

浏览器、Word、Drawio、客服、订单、模型和上线检查见 [验收手册](ACCEPTANCE_GUIDE.md)。这些验收并不都只能在 Windows 执行；本机验收与跨平台 CI、外部服务联调分别记录，不能互相代替。

## 选择环境方式

| 方式 | 适用情况 | 解释器使用 |
| --- | --- | --- |
| conda（你的主要路径） | 已用 Miniconda/Anaconda 管理环境 | `conda activate codemax` 后用 `python`、`python -m pip` |
| venv（可选） | 没有使用 conda，愿意用标准库创建环境 | 激活 `.venv` 后用 `python`、`python -m pip` |

两者选一个即可。PostgreSQL 服务和 Node 安装不由 Python 环境激活替代。全文 Windows 命令使用 cmd；不要混用 PowerShell `$env:`、Linux `source`/`export` 与 cmd 的 `set`。

## venv 备选步骤

仅供没有使用 conda 的情况；先确保系统 Python 为 3.11。已有 conda 环境则返回上面的主要指南，不在激活的 conda 中再嵌套 venv。

```cmd
cd /d C:\work\codemax_platform
python --version
python -m venv .venv
call .venv\Scripts\activate.bat
set PYTHONUTF8=1
python -c "import sys; print(sys.executable)"
python -m pip install -r requirements.txt
python -m pip check
npm ci
npm run build
```

之后共用 conda 指南中的配置、数据库、启动与测试步骤，但每次打开新 cmd 时，用 `call .venv\Scripts\activate.bat` 代替 `conda activate codemax`，退出用 `deactivate`。在独立验收目录使用原环境时，可用完整路径 `call C:\work\codemax_platform\.venv\Scripts\activate.bat`，不要求把环境复制过去。Conda 专有的 create/run/export 命令不适用于 venv。

## 常见问题

| 现象 | 先检查什么 |
| --- | --- |
| 缺包，或安装成功但启动仍缺包 | `python -c "import sys; print(sys.executable)"` 与 `python -m pip --version` 是否是同一个环境；IDE 也需选择它 |
| pip 尝试编译、报 Rust/MSVC | 核对是否误用新 Python/错误架构，先在隔离的 Python 3.11 环境安装仓库依赖，不随意降级安全修复版本 |
| psql 不存在或数据库连接失败 | PostgreSQL 服务、bin 路径、端口与 DB 配置；conda 激活不会启动服务 |
| “数据库已存在”之后数据没了 | full_init 仍会删表！立刻停止写入，按备份恢复，不再重复初始化 |
| 改 .env 没效果 | 是否从项目根启动、是否有同名环境变量覆盖、是否已重启 |
| 登录后没权限使用管理员功能 | 数据库角色必须是管理员，昵称不算权限；开发演示身份需先改默认密码 |
| 端口 8000 被占用 | 停止自己先前启动的实例或选新端口，同时更新本地 SITE_BASE_URL；不随意结束陌生进程 |
| conda activate 不可用 | 用安装器提供的 Anaconda/Miniconda Prompt；按 conda 指南初始化 cmd 后重新打开 |
| 文档门禁说多了未知文件 | 测试日志/环境导出放仓库外；不要关闭扫描、提交密钥或扩大忽略规则遮盖新源码 |
| Drawio 空白 | 核对 diagrams.net 网络可达性、浏览器 Console；这与被停用的爬虫 Chromium 不是同一功能 |

## 当前边界

- 存量升级先备份、停写，按实际缺失迁移执行；不重跑 full_init。
- mock 只是本地模拟付款；manual 需要本人核账；wechat 需要商户配置和真实回调验证。
- 人工支持通过 `/support/center` 的管理员/客户站内会话提供，不是已接入外部客服机构或自动通知。
- 安装浏览器不会解除动态抓取停用；对象存储、多副本等未实现能力不能当成“配置一下就完成”。

历史验收快照见 [第二批修复记录](SECOND_REPAIR_ACCEPTANCE.md) 和 [文档质量复核](DOCUMENTATION_QUALITY_REVIEW.md)；本机尚未执行的项目按验收手册逐项记录，不沿用旧截图/数字代替本次结果。
