from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """应用配置，从 .env 读取（模板见 .env.example）。"""

    DB_HOST: str = "localhost"
    DB_PORT: int = 5432
    DB_NAME: str = "codemax_db"
    DB_USER: str = "postgres"
    DB_PASSWORD: str = ""
    DATABASE_URL: str = ""  # 可选：直接指定 SQLAlchemy URL（测试用）
    SECRET_KEY: str = "dev-secret-change-me"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30

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
    LLM_SEMANTIC_THRESHOLD: float = 0.55

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
    SHOP_PRODUCT_AMOUNT: int = 19900
    # 待支付订单多久算超时（S5-01-1）。超时就关单并允许重新下单，
    # 解决 TD-109：过期二维码的订单被无限复用、扫了必失败。
    ORDER_EXPIRE_MINUTES: int = 30

    # 支付通道：wechat = 微信支付（生产必须用这个）；mock = 模拟收银台；
    #   manual = 展示静态收款码 + 管理员人工确认收款（S5-04）
    # mock 只用于本地开发与答辩演示，**开着就等于免费发货**，见 TECH_DECISIONS.md TD-124
    # manual 与 mock 的区别：mock 任何人都能点「确认支付」，manual 只有管理员能确认（TD-205）
    SHOP_PAY_MODE: str = "wechat"

    # manual 模式展示的收款码图片路径。**换成自己的收款码即可**，代码不用动。
    # 刻意指向 static 下的一个文件而不是把图片塞进配置：图片是二进制，
    # 配置项只该存「在哪」，不该存「是什么」。
    SHOP_MANUAL_QR: str = "/static/pay_qr.svg"

    # 云存储（S3-02）：local = 本地目录（开发/演示）；oss / cos 需密钥，尚未实现（TD-128）
    STORAGE_BACKEND: str = "local"
    STORAGE_LOCAL_ROOT: str = "storage"  # 本地后端的根目录（已 gitignore）
    STORAGE_PRODUCT_KEY: str = "product/codemax_package.zip"  # 商品文件的对象 key
    DOWNLOAD_URL_TTL: int = 300  # 预签名 URL 有效期（秒），S3-02-3

    # 每个用户最多存多少张流程图（TD-64）。删掉一张就腾出一个名额 ——
    # 配额只数**存活**的行，软删除的（回收站里的）不占。
    # 注意这只约束存活行数，回收站本身不会自动清空，见 TD-179。
    DIAGRAM_QUOTA: int = 50

    # 限流（TD-15）：/tools/* 刻意不设鉴权，任何人都能无限调用，所以必须限
    RATE_LIMIT_ENABLED: bool = True
    RATE_LIMIT_WINDOW: int = 60  # 窗口秒数
    RATE_LIMIT_TOOLS: int = 30  # DDL 解析 / Word 导出：每个 IP 每窗口次数
    RATE_LIMIT_LLM: int = 10  # LLM 端点更严：每次调用都花钱
    RATE_LIMIT_AUTH: int = 10  # 注册 / 登录：防在线爆破
    TRUST_PROXY_HEADERS: bool = False  # 只在可信反向代理之后才打开（TD-142）

    # ---- 部署与运维（S5-03）----
    # development / production。production 下会强制若干安全检查，见 app/startup_checks.py
    ENV: str = "development"
    LOG_LEVEL: str = "INFO"
    # HSTS 的 max-age（秒），默认一年。只在请求确实是 https 时才下发 ——
    # 在 http 上下发没有意义，还会把仍在用 http 的本地环境锁死一年。
    HSTS_MAX_AGE: int = 31536000

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def sqlalchemy_url(self) -> str:
        if self.DATABASE_URL:
            return self.DATABASE_URL
        return (
            f"postgresql+asyncpg://{self.DB_USER}:{self.DB_PASSWORD}"
            f"@{self.DB_HOST}:{self.DB_PORT}/{self.DB_NAME}"
        )


settings = Settings()
