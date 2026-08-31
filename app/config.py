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
