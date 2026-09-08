from typing import Literal
from urllib.parse import quote_plus

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，从 .env 读取（模板见 .env.example）。"""

    DB_HOST: str = "localhost"
    DB_PORT: int = Field(5432, ge=1, le=65535)
    DB_NAME: str = "codemax_db"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DATABASE_URL: str = ""  # 可选：直接指定 SQLAlchemy URL（测试用）
    SECRET_KEY: str = "dev-secret-change-me"
    ALGORITHM: str = "HS256"
    # 0 或负数会让签出来的令牌立刻过期，用户表现为「登录成功但下一秒就掉线」
    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(30, gt=0)

    # LLM（S2-01-2）：OpenAI 兼容接口，换 base_url 即可对接国产模型 / 本地 Ollama
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o-mini"
    # 向量模型必须单独配：对话模型不能打 /embeddings（S4-02-5 的语义 FAQ 检索用）。
    # 本地 Ollama 换成 nomic-embed-text 之类，与 LLM_MODEL 互不影响。
    LLM_EMBED_MODEL: str = "text-embedding-3-small"
    # 语义检索判定阈值（S4-02-5）。**必须跟着 embedding 模型走**：换模型
    # （text-embedding-3-small → nomic-embed-text → bge-m3）会让余弦分布整体
    # 漂移，沿用旧阈值要么永远不命中、要么乱命中。所以它是配置项而不是常量。
    # ⚠ 0.55 目前是按 text-embedding-3-small 的经验值，**尚未实测标定**，
    # 上线前跑 tests/test_faq_semantic.py 的标定用例重新量（TD-206）。
    # 余弦相似度只在 [0,1]（faq.semantic_search 里负值被截断为 0），所以越界值
    # 必然是误配，而且是**静默**的那种：>1 等于悄悄关掉语义检索，
    # <0 则几乎所有问句都被当成命中 FAQ —— 后者会把本该转人工的问题
    # 短路成一个自信的错答。配置项一旦暴露给 .env，就必须把范围一起约束住。
    LLM_SEMANTIC_THRESHOLD: float = Field(0.55, ge=0.0, le=1.0)

    # 站点对外地址（S2-02-1）：sitemap / robots / canonical 用，必须是绝对 URL
    SITE_BASE_URL: str = "https://codemax.top"

    # 微信支付 APIv3（S3-01）：六项缺一不可，没配齐下单接口直接 503
    WX_APPID: str = ""
    WX_MCHID: str = ""  # 商户号
    WX_SERIAL_NO: str = ""  # 商户 API 证书序列号
    WX_PRIVATE_KEY: str = ""  # apiclient_key.pem 的内容（PEM 文本）
    WX_API_V3_KEY: str = ""  # APIv3 密钥（32 字节），回调报文 AES-GCM 解密用
    WX_NOTIFY_URL: str = ""  # 支付结果回调地址，必须公网可达的 https
    WX_PLATFORM_CERT: str = ""  # 微信支付平台证书或微信支付公钥（PEM 文本），回调验签用

    # 商品：阶段三只有一个 SKU，金额单位是分
    SHOP_PRODUCT_NAME: str = "毕设服务"
    # 实测过：负值会被静默接受，直接生成负价订单（金额是分）。价格出错不该等到对账才发现
    SHOP_PRODUCT_AMOUNT: int = Field(19900, gt=0)
    # 待支付订单多久算超时（S5-01-1）。超时就关单并允许重新下单，
    # 解决 TD-109：过期二维码的订单被无限复用、扫了必失败。
    ORDER_EXPIRE_MINUTES: int = Field(30, gt=0)

    # 支付通道：wechat = 微信支付（生产必须用这个）；mock = 模拟收银台；
    #   manual = 展示静态收款码 + 管理员人工确认收款（S5-04）
    # mock 只用于本地开发与答辩演示，**开着就等于免费发货**，见 TECH_DECISIONS.md TD-124
    # manual 与 mock 的区别：mock 任何人都能点「确认支付」，manual 只有管理员能确认（TD-205）
    SHOP_PAY_MODE: str = "wechat"

    # manual 模式展示的收款码图片路径。**换成自己的收款码即可**，代码不用动。
    # 刻意指向 static 下的一个文件而不是把图片塞进配置：图片是二进制，
    # 配置项只该存「在哪」，不该存「是什么」。
    # manual 模式的静态收款码。默认指向仓库里提交的真收款码（2026-09-08 由用户
    # 上传并确认使用，见 app/static/pay_qr.png）。不想用时在 .env 里改回
    # /static/pay_qr.svg（占位图）即可。⚠️ 这是**个人**收款码，详见 TD-225。
    SHOP_MANUAL_QR: str = "/static/pay_qr.png"

    # 云存储（S3-02）：local = 本地目录（开发/演示）；oss / cos 需密钥，尚未实现（TD-128）
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_ROOT: str = "storage"  # 本地后端的根目录（已 gitignore）
    STORAGE_PRODUCT_KEY: str = "product/codemax_package.zip"  # 商品文件的对象 key
    DOWNLOAD_URL_TTL: int = Field(300, gt=0)  # 预签名 URL 有效期（秒），S3-02-3

    # 每个用户最多存多少张流程图（TD-64）。删掉一张就腾出一个名额 ——
    # 配额只数**存活**的行，软删除的（回收站里的）不占。
    # 注意这只约束存活行数，回收站本身不会自动清空，见 TD-179。
    DIAGRAM_QUOTA: int = Field(50, ge=0)  # 0 表示关闭云端保存，是合法配置

    # 限流（TD-15）：/tools/* 刻意不设鉴权，任何人都能无限调用，所以必须限
    RATE_LIMIT_ENABLED: bool = True
    # ⚠ **安全关键**：实测 `RATE_LIMIT_WINDOW=0`（或负数）会让 `Limiter.allow()`
    # 的滑动窗口把所有历史命中都弹出，于是 `len(hits) >= limit` 永远不成立 ——
    # 限流被**完全关掉且不报错**（fail-open）。这类值必须在启动期就挡住。
    RATE_LIMIT_WINDOW: int = Field(60, gt=0)  # 窗口秒数
    RATE_LIMIT_TOOLS: int = Field(30, gt=0)  # DDL 解析 / Word 导出：每个 IP 每窗口次数
    RATE_LIMIT_LLM: int = Field(10, gt=0)  # LLM 端点更严：每次调用都花钱
    RATE_LIMIT_AUTH: int = Field(10, gt=0)  # 注册 / 登录：防在线爆破
    TRUST_PROXY_HEADERS: bool = False  # 只在可信反向代理之后才打开（TD-142）

    # ---- 部署与运维（S5-03）----
    # development / production。production 下会强制若干安全检查，见 app/startup_checks.py
    # **必须是 Literal 而不是自由字符串**：`startup_checks.py` 与登录 Cookie 的 `secure`
    # 都是 `ENV == "production"` 精确比对，写成 "Production"/"prod" 时四项生产硬检查
    # 会**全部静默跳过**、Cookie 同时丢掉 Secure —— 实测过，且没有任何报错。
    # 这是「配置暴露给 .env 就必须约束取值」里最要紧的一条（TD-212 的同类问题）。
    ENV: Literal["development", "production"] = "development"
    LOG_LEVEL: str = "INFO"
    # HSTS 的 max-age（秒），默认一年。只在请求确实是 https 时才下发 ——
    # 在 http 上下发没有意义，还会把仍在用 http 的本地环境锁死一年。
    HSTS_MAX_AGE: int = Field(31536000, ge=0)  # 0 表示不下发 HSTS，是合法配置

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_url(self) -> str:
        """拼数据库连接串。**用户名与密码必须做 URL 转义。**

        为什么不能直接 f-string 拼：密码里只要有 `@ : / # ? %` 中任何一个，
        URL 的结构就被改写了。实测 `DB_PASSWORD='p@ss:w/rd#1'` 时 SQLAlchemy
        把端口解析成 `'w'`，抛 `ValueError: invalid literal for int() with base 10: 'w'`。

        更糟的是它**不一定报错**：`pass@word` 这种只会悄悄把 host / 密码解析成
        别的东西，于是连到错的机器、或者用错的密码反复认证失败 —— 现象与
        「数据库挂了」一模一样，极难排查。

        这不是理论问题：托管数据库（RDS / Cloud SQL / Supabase）自动生成的密码
        经常就带 `@ / #`，而在 `.env` 里直接写原值是最自然的用法。

        用 `quote_plus` 而不是 `quote`：连接串的 userinfo 段里空格必须编成 `+`
        而不是 `%20` 之外的形式，且 `/` 也要转义（`quote` 默认放行 `/`）。

        显式给了 `DATABASE_URL` 就直接原样返回 —— 那是用户自己写好的完整 URL，
        再转义一遍会把已经编好的 `%40` 变成 `%2540`（双重编码）。
        """
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{quote_plus(self.DB_USER)}:{quote_plus(self.DB_PASSWORD)}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
