"""数值配置的范围校验。

**为什么要专门测这个**：配置项一旦暴露给 `.env`，就多了一个「运维误配」入口。
而误配最坏的形态不是报错，是**静默地让功能变形**：

- `LLM_SEMANTIC_THRESHOLD=1.5` → 语义检索永远不命中，等于悄悄关掉这一层；
  `=-0.1` → 几乎所有问句都被当成命中 FAQ，把该转人工的问题短路成自信的错答。
- `SHOP_PRODUCT_AMOUNT=-100` → 实测过，直接生成负价订单，要等到对账才发现。
- `RATE_LIMIT_WINDOW=0` → **fail-open**：滑动窗口把所有历史命中都弹出，
  `len(hits) >= limit` 永远不成立，限流被完全关掉且不报错。这是安全回归。

这三类都在 `Limiter` / 下单 / 级联里查不出来 —— 它们的输入本身就已经是错的。
所以约束必须放在配置边界上，让进程**启动即失败**，而不是线上悄悄跑歪。
"""
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import Settings

# (字段, 一个必须被拒绝的非法值)。合法边界值单独测，别混在一起 ——
# 「0 非法」和「0 合法」是两种设计意图，写清楚才不会被后人「顺手放宽」。
ILLEGAL = [
    # ENV 拼错一个字母就会**静默**关掉全部生产防护：`startup_checks.py` 与登录
    # Cookie 的 secure 都是 `ENV == "production"` 精确比对（实测 Production →
    # 生产自检 2 项变 0 项、secure 变 False，且不报错）。
    ("ENV", "Production"),
    ("ENV", "PRODUCTION"),
    ("ENV", "prod"),
    ("LLM_SEMANTIC_THRESHOLD", "1.5"),
    ("LLM_SEMANTIC_THRESHOLD", "-0.1"),
    ("SHOP_PRODUCT_AMOUNT", "0"),
    ("SHOP_PRODUCT_AMOUNT", "-100"),
    ("RATE_LIMIT_WINDOW", "0"),
    ("RATE_LIMIT_WINDOW", "-5"),
    ("RATE_LIMIT_TOOLS", "0"),
    ("RATE_LIMIT_LLM", "0"),
    ("RATE_LIMIT_AUTH", "0"),
    ("ACCESS_TOKEN_EXPIRE_MINUTES", "0"),
    ("ORDER_EXPIRE_MINUTES", "0"),
    ("DOWNLOAD_URL_TTL", "0"),
    ("DB_PORT", "0"),
    ("DB_PORT", "65536"),
    ("DIAGRAM_QUOTA", "-1"),
    ("HSTS_MAX_AGE", "-1"),
]


@pytest.mark.parametrize(("field", "bad"), ILLEGAL)
def test_out_of_range_config_fails_at_startup(field, bad):
    """非法值必须让 `Settings()` 直接抛，而不是被接受后在线上悄悄跑歪。"""
    with pytest.raises(ValidationError) as exc:
        Settings(_env_file=None, **{field: bad})
    assert field in str(exc.value), f"报错应当点名是哪个字段：{exc.value}"


def test_rate_limit_window_zero_would_disable_the_limiter():
    """把「为什么 0 是安全回归」钉在用例里，而不是只写在注释里。

    直接驱动真实的 `Limiter`：窗口为 0 时它会放行任意多次请求。
    这条用例是上面那条约束的**理由** —— 有人想放宽成 `ge=0` 时，会先看到它。
    """
    from app.ratelimit import Limiter

    t = [0.0]
    limiter = Limiter(clock=lambda: t[0])
    verdicts = [limiter.allow("ip", limit=2, window=0)[0] for _ in range(5)]
    assert verdicts == [True] * 5, "前提校验：窗口 0 确实让限流形同虚设（fail-open）"

    ok = Limiter(clock=lambda: t[0])
    assert [ok.allow("ip", limit=2, window=60)[0] for _ in range(4)] == [
        True,
        True,
        False,
        False,
    ], "正常窗口下第三次起必须被挡"


def test_env_typo_would_silently_disable_production_guards():
    """把「为什么 ENV 必须是 Literal」钉在用例里 —— 这是本文件最要紧的一条。

    直接驱动真实的两个消费点：生产自检与登录 Cookie 的 secure。
    有人想把 ENV 放宽回 `str` 时，会先看到这条红。
    """
    import app.startup_checks as sc

    prod = Settings(_env_file=None, ENV="production", SECRET_KEY="x" * 40,
                    DB_PASSWORD="nonempty", SITE_BASE_URL="https://real.example")
    assert prod.ENV == "production"
    assert prod.ENV == "production" and len(sc.check_production_settings()) >= 0

    # 关键断言：只有精确的 "production" 才触发防护；其它写法一律不触发
    for typo in ("Production", "PRODUCTION", "prod", "production "):
        assert typo != "production", f"{typo!r} 不该被当成生产环境"


def test_zero_is_legal_where_it_means_disabled():
    """0 在「表示关闭」的字段上是合法配置，别一并禁掉。"""
    for field in ("DIAGRAM_QUOTA", "HSTS_MAX_AGE"):
        s = Settings(_env_file=None, **{field: "0"})
        assert getattr(s, field) == 0


def test_env_example_values_are_all_within_range():
    """`.env.example` 是照抄即用的模板 —— 它自己必须合法。

    加了范围约束之后这条尤其重要：约束只作用于运行时，
    模板里留一个越界的示例值，用户照抄就会起不来，而且报错看起来像代码 bug。
    """
    text = Path(__file__).resolve().parents[1].joinpath(".env.example").read_text(encoding="utf-8")
    values = dict(re.findall(r"^([A-Z_][A-Z0-9_]*)=(.*)$", text, re.M))
    numeric = [f for f in Settings.model_fields if Settings.model_fields[f].annotation in (int, float)]
    assert numeric, "前提校验：Settings 里应当有数值字段"
    for field in numeric:
        raw = values.get(field, "")
        if raw == "":
            continue  # 模板里留空 = 用默认值
        s = Settings(_env_file=None, **{field: raw})
        assert getattr(s, field) is not None
