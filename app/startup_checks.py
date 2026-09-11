"""启动时的生产环境自检（S5-03）。

**在启动时失败，而不是在用户下单时失败。** 这几项配错的后果都是"线上跑着但
行为是错的"，等发现时损失已经产生，所以宁可拒绝启动。

只在 `ENV=production` 时强制；开发环境保持默认值可用，否则本地根本起不来。
"""
from __future__ import annotations

import logging

from .config import settings

DEFAULT_SECRET = "dev-secret-change-me"

# A-12：HMAC 密钥的最短长度。旧检查只拦「等于默认值」，把默认值改成一个 8 位短串
# 就绕过去了 —— 而 HMAC 的强度取决于密钥熵，短密钥可被离线暴力破解，照样能伪造 JWT。
MIN_SECRET_KEY_LEN = 32

logger = logging.getLogger("codemax.startup")


class ProductionConfigError(RuntimeError):
    """生产配置不合规，拒绝启动。"""


def check_production_settings() -> list[str]:
    """返回问题列表；空列表代表通过。单独成函数是为了能直接单测，不必真启动应用。"""
    if settings.ENV != "production":
        return []
    problems: list[str] = []
    # TD-124：模拟支付通道开着就等于免费发货。默认值虽然是 wechat，
    # 但一次复制粘贴 .env 就可能带上去，后果是白送商品，必须硬拦。
    if settings.SHOP_PAY_MODE == "mock":
        problems.append(
            "SHOP_PAY_MODE=mock 在生产环境等于免费发货（TD-124）；请改为 wechat"
        )
    # 默认密钥签出来的 JWT 任何人都能伪造
    if settings.SECRET_KEY == DEFAULT_SECRET:
        problems.append(f"SECRET_KEY 仍是默认值 {DEFAULT_SECRET!r}，任何人都能伪造 JWT")
    # A-12：SECRET_KEY 太短。用 elif —— 默认值那条消息更可操作，
    # 同一个根因报两遍只是噪音。
    elif len(settings.SECRET_KEY) < MIN_SECRET_KEY_LEN:
        problems.append(
            f"SECRET_KEY 只有 {len(settings.SECRET_KEY)} 位，短于 {MIN_SECRET_KEY_LEN} 位；"
            "HMAC 密钥太短可被离线暴力破解，JWT 仍可被伪造"
        )
    # 限流关掉的话，不设鉴权的 /tools/* 可以被无限刷
    if not settings.RATE_LIMIT_ENABLED:
        problems.append("RATE_LIMIT_ENABLED=false：公开工具端点可被无限刷（TD-15）")
    # 在反向代理之后却不信任代理头，客户端 IP 会全部记成代理的 IP，限流形同虚设
    if not settings.TRUST_PROXY_HEADERS:
        problems.append(
            "TRUST_PROXY_HEADERS=false：若部署在反向代理之后，限流会把所有用户"
            "当成同一个 IP（TD-142）。确实不在代理之后才可忽略此项"
        )
    # A-12：DB_PASSWORD 为空。默认值就是 ""，`.env` 漏一行就是空。真库若开了 trust
    # 认证会**静默连上**一个没设密码的库；没开则是等用户下单时才连接失败。
    # 给了 DATABASE_URL 就不报 —— 那串 URL 里已经带了自己的凭证。
    if not settings.DB_PASSWORD and not settings.DATABASE_URL:
        problems.append(
            "DB_PASSWORD 为空：生产库必须设密码，或显式给出带凭证的 DATABASE_URL"
        )
    # A-12：SITE_BASE_URL 空或明文 http。预签名下载链接、HSTS、OAuth 回跳都拿它当
    # 基准，配错的表现是「站点跑得起来但链接全指向错的主机」。
    # ⚠️ 刻意**不**查「是否等于默认值」：默认值 https://codemax.top 就是真实生产
    # 域名，照 review 那样写会把真正的生产部署也判成不合规。
    if not settings.SITE_BASE_URL or not settings.SITE_BASE_URL.startswith("https://"):
        problems.append(
            f"SITE_BASE_URL={settings.SITE_BASE_URL!r} 必须是以 https:// 开头的完整地址："
            "预签名下载链接、HSTS 与 OAuth 回跳都以它为基准"
        )
    return problems


def check_production_warnings() -> list[str]:
    """返回**只告警、不拦启动**的问题；空列表代表没有。

    与 `check_production_settings` 分开，是因为这里的项都属于「可能是合法部署，
    但值得让运维确认一眼」—— 硬拦会逼运维去关自检，那比不检查更糟。
    """
    if settings.ENV != "production":
        return []
    warnings: list[str] = []
    # A-12：local 后端把商品文件放在容器本地磁盘。配上挂载卷、单实例部署是合法的
    # 生产形态，而这个配置项本身**看不出**卷挂没挂，所以只告警。
    if settings.STORAGE_BACKEND == "local":
        warnings.append(
            "STORAGE_BACKEND=local：商品文件落在容器本地磁盘，重新部署即丢失，"
            "且多副本之间不共享。确认已挂载持久卷、且只跑单实例才可忽略此项"
        )
    return warnings


def enforce_production_settings() -> None:
    for w in check_production_warnings():
        logger.warning("生产配置提醒：%s", w)
    problems = check_production_settings()
    if problems:
        raise ProductionConfigError(
            "生产环境配置检查未通过（ENV=production）：\n  - " + "\n  - ".join(problems)
        )
