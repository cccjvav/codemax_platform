# 应用装配与根目录配置

快速运行见 [根 README](../README.md)。这里解释入口与配置职责，不复制会过期的依赖版本表或手写行数。

## main.py：应用生命周期

导入配置与模块后配置 logging，执行生产自检，再创建 FastAPI、挂中间件、注册路由和静态目录。不是“在所有模块导入前配置日志”。
`lifespan`先等待生产数据库安全检查（账本/已知演示凭据/管理员），再做best-effort语义FAQ预热；finally在启动失败或退出时也关闭CPU池并dispose engine。预热失败可回落词袋，不代表所有模型增强已经成功启用。

`RequestLoggingMiddleware` 后注册，因此位于安全响应头中间件外层。业务路由来自 app/routers；site 的页面来自明确清单，不是吞掉其余 API 的通配路由。
生产关闭 docs/redoc/openapi；开发保留。`/static` 指向由 __file__ 确定的目录，Jinja HTML 页面仍走页面路由。

## 构建与依赖

| 文件 | 维护契约 |
| --- | --- |
| requirements.txt | Python 锁定依赖；当前 Docker 安装整份，测试/文档工具也会进入镜像，不能仅凭注释分段声称生产没安装它们 |
| package.json / package-lock.json | 前端脚本与锁文件；Node 用于构建/测试，生产由 Python 提供生成 JS |
| vite.config.mjs | 页面入口和输出规则；源码位于 app/frontend，产物与共用分块在 app/static/js；不得只复制入口 |
| Dockerfile | Python 应用容器；关闭 Uvicorn 代理头处理，由应用执行可信对端规则；不是包含 Nginx 的完整生产环境 |
| docker-compose.yml | 隔离示例的应用/数据库与卷，默认回环绑定；取消自动SQL挂载，显式CLI初始化/迁移；DATABASE_URL清空并固定示例db，防误连外部库 |
| ruff.toml | 已启用静态规则、框架调用白名单和有理由的例外；不以关闭规则代替修复 |
| pytest.ini / .coveragerc | 测试运行与覆盖率口径；配置文件存在不代表本次测过覆盖率 |
| .env.example | 非秘密模板；真实 .env 不入库；部分 Compose 变量不是 Settings 字段 |
| .gitignore / .dockerignore / .gitattributes | Git/镜像排除与文本规范化；SQL强制LF保证校验和，镜像保留database init供启动检查；Git忽略不等于镜像自动忽略 |

## 修改影响

改路由注册核对导航、API 表和运行时路由对比；改依赖核对锁文件、构建与扫描；改部署核对端口、真实代理对端、持久卷、启动自检和迁移。
源码文件表与 SHA 在根 README 自动区，逐函数签名见文档站；这些只是结构证据，人工职责说明仍需与当前源码一起评审。

## 2026-09-15 交叉审查增量

Docker入口禁用默认Uvicorn access log，防签名下载查询串进入第二套日志；其他启动方式和代理需单独设置。Vite入口固定名，拆分chunk逻辑名可含hash；不能对所有固定资源宣称immutable安全。
