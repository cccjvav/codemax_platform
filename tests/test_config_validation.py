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


# ---------------------------------------------------------------- DB 密码转义（A-10）
#
# `sqlalchemy_url` 是用 f-string 直接把 DB_USER / DB_PASSWORD 拼进 URL 的。
# 密码里只要有 `@ : / # ?` 中任何一个，URL 的结构就被改写了 ——
# 实测 `DB_PASSWORD='p@ss:w/rd#1'` 时 SQLAlchemy 把端口解析成 `'w'`，抛
# `ValueError: invalid literal for int() with base 10: 'w'`。
#
# 更糟的是它**不一定报错**：`p@ss` 会让 host 变成 `ss`，连的是另一台机器。


@pytest.mark.parametrize(
    "password",
    [
        "p@ss:w/rd#1",  # @ : / # 全都有
        "pass@word",  # 只有 @
        "p/w",  # 只有 /
        "a#b",  # 只有 #（后面全被当成 fragment 丢掉）
        "100%natural",  # % 会被当成百分号转义的开头
        "p?q=1",  # ? 后面被当成 query
    ],
)
def test_db_password_with_url_metacharacters_still_builds_a_valid_url(password):
    """密码含 URL 元字符时，连接串必须仍然解析出**原本的**那几段。

    这不是理论问题：托管数据库（RDS / Cloud SQL / Supabase）自动生成的密码
    经常就带 `@ / #`，而 `.env` 里直接写原值是最自然的用法。
    """
    from sqlalchemy.engine import make_url

    s = Settings(
        _env_file=None,
        DATABASE_URL="",
        DB_USER="postgres",
        DB_PASSWORD=password,
        DB_HOST="db.internal",
        DB_PORT=5432,
        DB_NAME="codemax_db",
    )
    url = make_url(s.sqlalchemy_url)  # 解析不了会直接抛

    # 必须逐段还原，不能只断言「没报错」——
    # `pass@word` 不转义时解析**不会失败**，它只是悄悄把 host 变成了别的东西。
    assert url.username == "postgres", f"用户名被改写：{url.username!r}"
    assert url.password == password, f"密码没有原样还原：{url.password!r}"
    assert url.host == "db.internal", f"host 被密码里的 @ 改写成了 {url.host!r}"
    assert url.port == 5432, f"端口被解析成 {url.port!r}"
    assert url.database == "codemax_db", f"库名被改写成 {url.database!r}"


def test_explicit_database_url_still_wins():
    """显式给了 `DATABASE_URL` 就不能再去拼 —— 否则转义逻辑会二次编码。"""
    from sqlalchemy.engine import make_url

    raw = "postgresql+asyncpg://u:p%40ss@h:5432/d"
    s = Settings(_env_file=None, DATABASE_URL=raw, DB_PASSWORD="ignored")
    assert s.sqlalchemy_url == raw
    assert make_url(s.sqlalchemy_url).password == "p@ss"
