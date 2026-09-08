"""S5-03 运维部分：安全响应头、健康探针、生产自检、CPU 池兜底。"""
import inspect
import re
import time
from pathlib import Path

import pytest

from app.config import settings
from app.database import get_db
from app.middleware import CONTENT_SECURITY_POLICY
from app.startup_checks import (
    DEFAULT_SECRET,
    ProductionConfigError,
    check_production_settings,
    check_production_warnings,
    enforce_production_settings,
)

ROOT = Path(__file__).resolve().parent.parent


# ============================================================ 安全响应头


@pytest.mark.asyncio
async def test_security_headers_present_on_every_response(client):
    r = await client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert r.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "Content-Security-Policy" in r.headers


@pytest.mark.asyncio
async def test_security_headers_also_on_error_responses(client):
    """404/422 也要带头 —— 攻击者常拿错误页做文章，中间件不能只管成功路径。"""
    for path in ("/no-such-page", "/support/ask"):
        r = await client.get(path)
        assert r.status_code in (404, 405)
        assert r.headers["X-Content-Type-Options"] == "nosniff"


@pytest.mark.asyncio
async def test_no_hsts_over_plain_http(client):
    """http 上不能下发 HSTS，否则还在用 http 的环境会被浏览器锁死一年。"""
    r = await client.get("/")
    assert "Strict-Transport-Security" not in r.headers


@pytest.mark.asyncio
async def test_hsts_sent_when_proxy_says_https(client, monkeypatch):
    """反向代理终止 TLS 时，靠 X-Forwarded-Proto 判断（且只在信任代理头时才信）。"""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", True)
    r = await client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert r.headers["Strict-Transport-Security"].startswith("max-age=")
    assert "includeSubDomains" in r.headers["Strict-Transport-Security"]


@pytest.mark.asyncio
async def test_hsts_not_honoured_from_untrusted_proxy_header(client, monkeypatch):
    """不信任代理头时，伪造 X-Forwarded-Proto 也不能骗出 HSTS。"""
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    r = await client.get("/", headers={"X-Forwarded-Proto": "https"})
    assert "Strict-Transport-Security" not in r.headers


def test_csp_blocks_the_dangerous_defaults():
    for directive in ("object-src 'none'", "base-uri 'none'", "frame-ancestors 'self'"):
        assert directive in CONTENT_SECURITY_POLICY


def test_every_external_origin_used_by_frontend_is_allowed_by_csp():
    """反向校验：模板与静态资源里出现的每个外部域，都必须在 CSP 白名单里。

    没有这条测试，将来谁加了新 CDN 忘了改 CSP，结果不是测试红，
    而是上线后页面白屏 —— 那种故障最难查，因为 HTML 是 200。
    """
    used: set[str] = set()
    for pattern in ("app/templates/*.html", "app/static/*.js", "app/static/*.html"):
        for f in ROOT.glob(pattern):
            used |= set(re.findall(r"https?://[A-Za-z0-9.-]+", f.read_text(encoding="utf-8")))
    assert used, "没扫到任何外部源，说明 glob 路径写错了"
    missing = {origin for origin in used if origin not in CONTENT_SECURITY_POLICY}
    assert not missing, f"这些外部源被前端用到但不在 CSP 白名单里：{sorted(missing)}"


def test_csp_allows_inline_scripts_because_templates_still_need_them():
    """四个模板都还有内联 <script>，所以现在必须保留 'unsafe-inline'（TD-163）。

    这条测试的作用是把「临时妥协」钉在明处：等哪天把内联脚本都外置了，
    这条会红，提醒把 'unsafe-inline' 去掉。
    """
    inline = [f.name for f in (ROOT / "app" / "templates").glob("*.html")
              if re.search(r"<script(?![^>]*\bsrc=)[^>]*>", f.read_text(encoding="utf-8"))]
    assert inline, "已经没有内联脚本了？那就可以收紧 CSP，请删掉这条测试"
    assert "'unsafe-inline'" in CONTENT_SECURITY_POLICY


# ============================================================ 请求日志与 request id


@pytest.mark.asyncio
async def test_request_id_returned_and_unique(client):
    a = (await client.get("/")).headers["X-Request-ID"]
    b = (await client.get("/")).headers["X-Request-ID"]
    assert a and b and a != b, "每个请求应有各自的 request id"


@pytest.mark.asyncio
async def test_incoming_request_id_is_preserved(client):
    """上游网关已经给了 id 就沿用它，这样一条链路能串起来查。"""
    r = await client.get("/", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


@pytest.mark.asyncio
async def test_requests_are_logged_with_status_and_duration(client, caplog):
    import logging

    with caplog.at_level(logging.INFO, logger="codemax.access"):
        await client.get("/healthz")
    text = caplog.text
    assert "GET /healthz" in text and "200" in text
    assert "rid=" in text


# ============================================================ 健康探针


@pytest.mark.asyncio
async def test_healthz_and_legacy_health_both_work(client):
    """`/health` 保留为别名：README 与既有测试都在用（TD-164）。"""
    for path in ("/healthz", "/health"):
        r = await client.get(path)
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_readyz_reports_ready_when_db_is_up(client):
    r = await client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_readyz_returns_503_not_500_when_db_is_down(client):
    """探针必须把异常吞成 503：抛出去会变成 500，编排器就分不清是应用坏了还是库坏了。"""

    class BrokenSession:
        async def execute(self, *_a, **_kw):
            raise RuntimeError("connection refused")

    async def broken_db():
        yield BrokenSession()

    from main import app as fastapi_app

    fastapi_app.dependency_overrides[get_db] = broken_db
    try:
        r = await client.get("/readyz")
    finally:
        fastapi_app.dependency_overrides.pop(get_db, None)
    assert r.status_code == 503
    assert "database" in r.text
    assert "RuntimeError" in r.text, "要带上异常类型，否则运维只看到 unavailable"


def test_liveness_probe_does_not_touch_the_database():
    """存活探针**不能**查库：库一抖就把健康实例全重启，等于把故障放大成雪崩。"""
    src = (ROOT / "app" / "routers" / "health.py").read_text(encoding="utf-8")
    healthz_src = src.split("async def healthz")[1].split("async def readyz")[0]
    assert "get_db" not in healthz_src and "text(" not in healthz_src


# ============================================================ 生产自检


def test_development_env_passes_without_complaint(monkeypatch):
    monkeypatch.setattr(settings, "ENV", "development")
    assert check_production_settings() == []


def _clean_prod(monkeypatch, **over):
    """造一套**完全合规**的生产配置，再按需覆盖某一项。

    每条用例只测一个字段，其余都保持合规 —— 否则「新增一条检查」会被别的字段
    顺带报出来，看不出到底是谁在响。
    """
    base = {
        "ENV": "production",
        "SHOP_PAY_MODE": "wechat",
        "SECRET_KEY": "a-real-long-random-secret-with-32-chars-min",
        "RATE_LIMIT_ENABLED": True,
        "TRUST_PROXY_HEADERS": True,
        "DB_PASSWORD": "not-empty",
        "DATABASE_URL": "",
        "SITE_BASE_URL": "https://codemax.top",
        "STORAGE_BACKEND": "oss",
    }
    for k, v in {**base, **over}.items():
        monkeypatch.setattr(settings, k, v)


def test_clean_production_config_passes_every_check(monkeypatch):
    """反方向护栏：合规配置**一条都不该报**。

    这条最容易被忽略，但它才是防止自检变成「狼来了」的关键 —— 检查项越加越多，
    只要有一条在正常部署下也报，运维就会习惯性忽略整个列表，那比没有检查更糟。
    """
    _clean_prod(monkeypatch)
    assert check_production_settings() == []


def test_empty_db_password_in_production_is_rejected(monkeypatch):
    """A-12：DB_PASSWORD 为空。

    默认值就是 `""`，而 `.env` 漏一行就是空。真库若开了 trust 认证会**静默连上**
    一个没设密码的库；若没开，则是等用户下单时才连接失败。给了 DATABASE_URL
    就不报 —— 那串 URL 里已经带了自己的凭证。
    """
    _clean_prod(monkeypatch, DB_PASSWORD="")
    problems = check_production_settings()
    assert len(problems) == 1 and "DB_PASSWORD" in problems[0]

    # 逃生舱：显式给了 DATABASE_URL 就不该再报
    _clean_prod(monkeypatch, DB_PASSWORD="", DATABASE_URL="postgresql+asyncpg://u:p@h/db")
    assert check_production_settings() == []


def test_short_secret_key_in_production_is_rejected(monkeypatch):
    """A-12：SECRET_KEY 太短。

    旧检查只拦「等于默认值」，把默认值改成一个 8 位短串就绕过去了 —— 而 HMAC 的
    强度取决于密钥熵，短密钥可被离线暴力破解，照样能伪造 JWT。
    """
    _clean_prod(monkeypatch, SECRET_KEY="short123")
    problems = check_production_settings()
    assert len(problems) == 1 and "SECRET_KEY" in problems[0]


def test_insecure_site_base_url_in_production_is_rejected(monkeypatch):
    """A-12：SITE_BASE_URL 为空或不是 https。

    ⚠️ 刻意**不**检查「是否等于默认值」：默认值 `https://codemax.top` 就是真实
    生产域名，照 review 那样写会把真正的生产部署也判成不合规。只查客观不安全
    的两种：空串（预签名下载链接与 HSTS 都会指向错误主机）、http（明文）。
    """
    _clean_prod(monkeypatch, SITE_BASE_URL="")
    assert any("SITE_BASE_URL" in x for x in check_production_settings())

    _clean_prod(monkeypatch, SITE_BASE_URL="http://codemax.top")
    assert any("SITE_BASE_URL" in x for x in check_production_settings())


def test_local_storage_backend_warns_but_does_not_block(monkeypatch, caplog):
    """A-12：STORAGE_BACKEND=local 在生产只**告警**，不拦启动。

    这是我对 review 建议的**刻意偏离**：local 后端配上挂载卷、单实例部署是合法的
    生产形态，而这个配置项本身**看不出**卷挂没挂。硬拦会把合法部署也挡在门外，
    于是运维只能去关自检 —— 那比不检查更糟。所以走 logger 告警。
    """
    _clean_prod(monkeypatch, STORAGE_BACKEND="local")
    assert check_production_settings() == [], "不该拦启动"
    assert any("STORAGE_BACKEND" in w for w in check_production_warnings()), "但必须告警"

    # 换成 oss 就不该再告警
    _clean_prod(monkeypatch, STORAGE_BACKEND="oss")
    assert check_production_warnings() == []


def test_production_with_all_defaults_is_rejected(monkeypatch):
    _clean_prod(monkeypatch)  # 先全部合规，再逐项打回不合规，才能数清是谁在响
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")
    monkeypatch.setattr(settings, "SECRET_KEY", DEFAULT_SECRET)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    monkeypatch.setattr(settings, "TRUST_PROXY_HEADERS", False)
    monkeypatch.setattr(settings, "DB_PASSWORD", "")
    problems = check_production_settings()
    # A-12 之后是 5 条：多出来的 DB_PASSWORD 是本轮新增
    assert len(problems) == 5
    joined = "\n".join(problems)
    for keyword in (
        "SHOP_PAY_MODE", "SECRET_KEY", "RATE_LIMIT_ENABLED", "TRUST_PROXY_HEADERS", "DB_PASSWORD"
    ):
        assert keyword in joined


def test_mock_pay_in_production_is_the_first_thing_reported(monkeypatch):
    """TD-124：模拟支付开着＝免费发货，这是后果最严重的一条，必须报出来。

    注意：本条**不能**再像以前那样只覆盖 4 个字段 —— A-12 之后 DB_PASSWORD、
    SITE_BASE_URL 也参与判定，不显式设成合规就会被顺带报出来，`len == 1` 就失去
    了「只有支付这一条在响」的含义。所以改用 `_clean_prod` 把全部字段钉死。
    """
    _clean_prod(monkeypatch, SHOP_PAY_MODE="mock")
    problems = check_production_settings()
    assert len(problems) == 1 and "免费发货" in problems[0]


def test_enforce_raises_only_in_production(monkeypatch):
    _clean_prod(monkeypatch, SHOP_PAY_MODE="mock")
    with pytest.raises(ProductionConfigError):
        enforce_production_settings()

    _clean_prod(monkeypatch)
    enforce_production_settings()  # 不该抛


def test_enforce_is_a_noop_in_development(monkeypatch):
    """开发环境必须能带着默认值起来，否则本地根本没法跑。"""
    monkeypatch.setattr(settings, "ENV", "development")
    monkeypatch.setattr(settings, "SHOP_PAY_MODE", "mock")
    monkeypatch.setattr(settings, "SECRET_KEY", DEFAULT_SECRET)
    enforce_production_settings()


# ============================================================ CPU 池


@pytest.mark.asyncio
async def test_cpu_pool_returns_same_result_as_direct_call():
    from app.cpu_pool import run_cpu_bound
    from app.tools.sql_ddl import parse_ddl

    ddl = "CREATE TABLE a (id INT PRIMARY KEY, b VARCHAR(10));"
    assert await run_cpu_bound(parse_ddl, ddl) == parse_ddl(ddl)


@pytest.mark.asyncio
async def test_process_pool_failure_falls_back_to_threadpool(monkeypatch):
    """进程池不可用时要退化成线程池（慢但不坏），不能让导出功能直接 500。

    Windows 用 spawn 起子进程、容器可能限进程数，这些都是真实会发生的。
    """
    import app.cpu_pool as cpu_pool

    cpu_pool._executor = None
    cpu_pool._broken = False

    def explode(*a, **kw):
        raise OSError("fork not permitted in this container")

    monkeypatch.setattr(cpu_pool, "ProcessPoolExecutor", explode)
    from app.tools.sql_ddl import parse_ddl

    ddl = "CREATE TABLE a (id INT PRIMARY KEY);"
    assert await cpu_pool.run_cpu_bound(parse_ddl, ddl) == parse_ddl(ddl)
    assert cpu_pool._broken is True, "坏过一次就该记住，别每个请求都再付一次失败开销"
    cpu_pool._executor = None
    cpu_pool._broken = False


def test_shutdown_tolerates_being_called_twice():
    from app import cpu_pool

    cpu_pool.shutdown()
    cpu_pool.shutdown()  # 不该抛


# ============================================================ 部署产物


def test_dockerfile_pins_python_and_does_not_run_as_root():
    f = ROOT / "Dockerfile"
    assert f.exists(), "缺 Dockerfile"
    text = f.read_text(encoding="utf-8")
    assert re.search(r"FROM python:3\.11", text), "必须钉在 3.11（pgserver 无 3.13 发行版）"
    assert re.search(r"^USER ", text, re.M), "不能以 root 运行"
    assert '"0.0.0.0"' in text, "容器里必须监听 0.0.0.0，否则宿主机连不进来"
    assert "requirements.txt" in text


def test_dockerfile_does_not_copy_secrets_or_venv():
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert ".dockerignore" in (ROOT / ".dockerignore").name or (ROOT / ".dockerignore").exists()
    ignore = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    for must in (".env", ".venv", ".git"):
        assert must in ignore, f".dockerignore 少了 {must}，会把密钥/虚拟环境打进镜像"
    assert "COPY .env" not in text, "绝不能把 .env 打进镜像"


# ============================================================ 依赖安全（TD-200）


def test_multipart_content_type_redos_is_patched():
    """`python-multipart` 必须 ≥ 0.0.26：Content-Type 头的 ReDoS 不能回来（TD-200）。

    这条测试的由来是实测：装 0.0.6 时，一个约 60 字节的畸形 `Content-Type` 头
    就能让**主事件循环**卡住数秒 —— 反斜杠每多 4 个耗时约 ×7：

        24 个 → 0.013s    28 个 → 0.089s    32 个 → 0.59s    36 个 → 4.04s

    而且这条路径**真的可达**：给 `/auth/login` 发 multipart 头时，stderr 会打出
    `multipart.multipart` 自己的日志。本项目没有任何 `UploadFile` 端点，
    但 Starlette 的 `request.form()` 照样会把 multipart 头交给它解析。

    为什么钉 0.0.26 而不是 0.0.7：0.0.7 只修了 Content-Type ReDoS 这一个，
    之后还有 0.0.18（畸形 boundary 逐字节跳过 + 每次记一条日志）、
    0.0.26（超大 preamble/epilogue）两处同类 DoS。

    阈值取 1 秒而不是贴近实测值：修复后是 0.0000s、修复前是 4.04s，
    中间差四个数量级，1 秒足够宽松又足够灵敏，不会因机器快慢抖动误报。
    """
    try:
        from python_multipart import multipart as mm
    except ImportError:  # 0.0.18 之前只有旧模块名
        from multipart import multipart as mm

    payload = b'multipart/form-data; boundary=x; filename="' + b"\\" * 36 + b"x"
    started = time.monotonic()
    mm.parse_options_header(payload)
    elapsed = time.monotonic() - started
    assert elapsed < 1.0, (
        f"Content-Type 解析耗时 {elapsed:.3f}s —— python-multipart 疑似回退到有 ReDoS 的版本，"
        "请确认 requirements.txt 里钉的是 >=0.0.26"
    )


def test_python_multipart_pinned_above_known_cve_versions():
    """requirements.txt 里的 `python-multipart` 必须钉在已知 CVE 全部修完的版本上。

    与上一条互补：上一条测**运行时行为**，这一条测**声明**，
    防止有人改了 requirements 却没重装环境（那样上一条会是绿的）。
    """
    line = next(
        (ln for ln in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
         if ln.strip().startswith("python-multipart")),
        None,
    )
    assert line, "requirements.txt 里找不到 python-multipart"
    m = re.search(r"python-multipart\s*==\s*([0-9.]+)", line)
    assert m, f"python-multipart 必须钉死版本（AGENTS.md：依赖全部钉死），实际写法：{line!r}"
    got = tuple(int(x) for x in m.group(1).split("."))
    assert got >= (0, 0, 26), (
        f"python-multipart=={m.group(1)} 仍受已公布 CVE 影响，最低要 0.0.26（TD-200）"
    )

# ---------------------------------------------------------------- 容器运维（A-15）
#
# ⚠️ 先纠正 review 的一处误判：它说「无 `.dockerignore`」—— 实际**有**，
# 而且上面 `test_dockerfile_does_not_copy_secrets_or_venv` 已经在守它。
# 真正缺的是下面这两条。


def test_dockerfile_has_a_healthcheck_against_a_real_endpoint():
    """镜像必须自带 HEALTHCHECK，且打的是**真实存在**的探针端点。

    没有 HEALTHCHECK 时，编排器只能靠「进程还在不在」判断健康 ——
    而进程活着但事件循环被堵死、或数据库连接池耗尽时，进程照样在。
    于是坏副本一直留在负载均衡里接流量，滚动发布也不会被判定失败。

    这里顺带钉住端点名：HEALTHCHECK 打一个不存在的路径是最常见的写法错误，
    它会让**每一个**健康检查都失败，反而把好副本全部重启。
    """
    text = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    lines = text.splitlines()
    idx = [k for k, s in enumerate(lines) if s.startswith("HEALTHCHECK")]
    assert idx, "Dockerfile 缺 HEALTHCHECK 指令"

    # ⚠️ HEALTHCHECK 通常写成续行（行尾反斜杠），CMD 在下一行。
    # 只取匹配到的那一行会漏掉端点 —— 必须把续行拼回来再解析。
    k = idx[0]
    line = lines[k].rstrip()
    while line.endswith("\\") and k + 1 < len(lines):
        line = line[:-1].rstrip() + " " + lines[k + 1].strip()
        k += 1

    probe = re.search(r"(/health\w*)", line)
    assert probe, f"HEALTHCHECK 必须打一个具体端点，实际：{line}"

    from app.routers import health

    paths = {r.path for r in health.router.routes}
    assert probe.group(1) in paths, (
        f"HEALTHCHECK 打的 {probe.group(1)!r} 不是真实端点（health 路由有 {sorted(paths)}）—— "
        "这样写会让每一次健康检查都失败，好副本反而被反复重启。"
    )
    # python:3.11-slim 里没有 curl 也没有 wget，必须用自带的 python 发请求
    assert "curl" not in line and "wget" not in line, (
        "python:3.11-slim 不含 curl/wget，这样写 HEALTHCHECK 永远返回非 0"
    )


async def test_shutdown_disposes_the_engine():
    """退出时必须 `engine.dispose()`，否则连接不会干净归还。

    `cpu_pool.shutdown()` 已经在 lifespan 里了，但连接池没有对应动作。
    进程被 SIGTERM 时，池里的连接是被**硬断开**的 —— PostgreSQL 那边会留下
    一堆 `idle in transaction` 直到超时才回收。滚动发布频繁时，
    这些悬挂连接会把 `max_connections` 吃满，新副本起不来。
    """
    import main

    src = inspect.getsource(main.lifespan)
    assert "dispose" in src, (
        "lifespan 的收尾里没有 engine.dispose() —— 连接会被硬断开，"
        "在 PostgreSQL 侧留下悬挂连接直到超时。"
    )


# ---------------------------------------------------------------- 依赖漏洞（P1-7）


def test_python_jose_is_not_downgraded_below_the_cve_fix():
    """`python-jose` 必须 ≥ 3.4.0。

    3.3.0 带两条已确认的 CVE，都在 3.4.0 修复：
      · **CVE-2024-33663** —— OpenSSH ECDSA 等密钥格式的**算法混淆**，
        可以用公钥签名（GHSA-6c5p-j8vq-pqhj）
      · **CVE-2024-33664** —— JWE 高压缩比解压 DoS（3.4.0 起限制 250 KiB）

    本站用 python-jose 签发/校验**登录令牌**，算法混淆这类问题正好打在它的核心用途上。

    为什么除了 CI 的 pip-audit 还要在这里再钉一道：CI 可能被跳过、
    分支保护可能没开，而这条测试跟着每一次 `pytest` 跑 ——
    本地降级就会立刻发现，不用等推上去。
    """
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    m = re.search(r"^python-jose\[cryptography\]==(\d+)\.(\d+)\.(\d+)", req, re.M)
    assert m, "requirements.txt 里找不到 python-jose 的钉版本行"
    version = tuple(int(x) for x in m.groups())
    assert version >= (3, 4, 0), (
        f"python-jose 被钉在 {'.'.join(m.groups())}，低于 3.4.0 —— "
        "会带回 CVE-2024-33663（算法混淆）与 CVE-2024-33664（JWE 解压 DoS）。"
    )


def test_ci_has_a_dependency_audit_job():
    """CI 里必须有依赖漏洞扫描。

    `requirements.txt` 是**手工钉版本**的 —— 钉死之后没有任何机制会在上游
    披露新 CVE 时通知我们。本次审查就是这么发现 python-jose 3.3.0 那两条的：
    它们在文件里躺了很久，没人知道。
    """
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "pip-audit" in ci, "ci.yml 里没有 pip-audit —— 新披露的 CVE 不会被发现"
    assert "--strict" in ci, (
        "pip-audit 必须带 --strict：拿不到漏洞数据时要失败，"
        "否则「扫描器没报错」和「扫描器没扫到」区分不开。"
    )
