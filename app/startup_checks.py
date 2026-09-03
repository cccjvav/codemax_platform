"""启动时的生产环境自检（S5-03）。

**在启动时失败，而不是在用户下单时失败。** 这几项配错的后果都是"线上跑着但
行为是错的"，等发现时损失已经产生，所以宁可拒绝启动。

只在 `ENV=production` 时强制；开发环境保持默认值可用，否则本地根本起不来。
"""
from __future__ import annotations

from .config import settings

DEFAULT_SECRET = "dev-secret-change-me"


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
    # 限流关掉的话，不设鉴权的 /tools/* 可以被无限刷
    if not settings.RATE_LIMIT_ENABLED:
        problems.append("RATE_LIMIT_ENABLED=false：公开工具端点可被无限刷（TD-15）")
    # 在反向代理之后却不信任代理头，客户端 IP 会全部记成代理的 IP，限流形同虚设
    if not settings.TRUST_PROXY_HEADERS:
        problems.append(
            "TRUST_PROXY_HEADERS=false：若部署在反向代理之后，限流会把所有用户"
            "当成同一个 IP（TD-142）。确实不在代理之后才可忽略此项"
        )
    return problems


def enforce_production_settings() -> None:
    problems = check_production_settings()
    if problems:
        raise ProductionConfigError(
            "生产环境配置检查未通过（ENV=production）：\n  - " + "\n  - ".join(problems)
        )
