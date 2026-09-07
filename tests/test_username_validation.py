# tests/test_username_validation.py
#
# 注册用户名的格式校验（2026-09-06 全仓体检的 A-5；报告已归档，见 HANDOVER.md「历次 code review 报告的处置与归档」）。
#
# 改动前 `RegisterIn.username` 只有 `min_length=3, max_length=50`，
# 于是这些全部 201 注册成功（实测）：
#   '   '                  空白名 —— 管理端列表里看不见、也搜不到
#   'a\nb\tc'              控制字符 —— 一旦进日志就能伪造日志行
#   '<script>x</script>'   标记语言 —— 任何非 HTML 上下文（JSON、纯文本导出、
#                          邮件、终端）里都是活着的注入载荷
#   'Admin' / 'ADMIN'      与种子管理员 `admin` 撞脸 —— 登录是精确匹配
#                          （`User.username == form.username`，PostgreSQL 的
#                          VARCHAR `=` 区分大小写），所以这是一个**独立账号**，
#                          专门用来在界面上冒充管理员。
"""A-5：用户名格式与保留名。"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio(loop_scope="session")


# ---------------------------------------------------------------- 非法字符


@pytest.mark.parametrize(
    "username",
    [
        "   ",  # 全空白
        "a\nb\tc",  # 控制字符
        "<script>x</script>",  # 标记语言
        "  bob  ",  # 首尾空白（看起来是 bob，实际是另一个账号）
        "bo b",  # 中间空格
        "用户 名",  # 中文里夹空格，同样不可见
        "bob;DROP",  # 引号/分号类元字符
        "bob'or'1",  # 经典注入形状（这里不是 SQL 注入，但没理由让它进库）
    ],
)
async def test_username_with_illegal_characters_is_rejected(client, username):
    """只允许「看得见的字母数字下划线连字符（含中文）」。

    这不是为了防 SQL 注入 —— 全站用 SQLAlchemy 参数化查询，注入本来就打不进去。
    真正的原因是这三条：
      ① **可辨识性**：`'   '` 和 `'  bob  '` 在管理端列表里与正常账号无法区分，
         出了钱货纠纷根本查不到人。
      ② **上下文安全**：用户名会被渲染到 HTML（OAuth 同意页 `{{ username }}`）、
         写进 JSON 响应、可能进日志与导出文件。Jinja 的 autoescape 只保得住
         HTML 这一处，其它上下文全靠「入库前就不许有这些字符」。
      ③ **日志完整性**：`\\n` 能让攻击者在日志里伪造整行记录。
    """
    r = await client.post("/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 422, (
        f"用户名 {username!r} 竟然注册成功（{r.status_code}）—— 格式校验没生效。"
    )
    # 必须明确说明原因，不能只回一个光秃秃的 422
    assert "用户名" in r.text, f"错误信息应指明是用户名的问题，实际：{r.text[:200]}"


# ---------------------------------------------------------------- 保留名


@pytest.mark.parametrize("username", ["admin", "Admin", "ADMIN", "AdMiN"])
async def test_admin_username_is_reserved(client, username):
    """`admin` 是保留名，且**大小写不敏感**。

    种子数据里管理员就叫 `admin`（`database init/full_init.sql`）。
    而登录用的是精确匹配（`User.username == form.username`），
    PostgreSQL 的 VARCHAR `=` 区分大小写 —— 所以 `Admin` 是一个**完全独立的账号**，
    却能注册成功。它唯一的用途就是在界面上冒充管理员：
    任何显示用户名的地方（OAuth 同意页、管理端列表）看起来都是「admin 本人」。

    这类「同形异体」冒充是 OWASP 明确列出的账户枚举/冒充向量。
    """
    r = await client.post("/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 422, (
        f"{username!r} 竟然注册成功（{r.status_code}）—— 与种子管理员撞脸的账号必须挡掉。"
    )


# ---------------------------------------------------------------- 合法名必须照收


@pytest.mark.parametrize(
    "username",
    ["alice", "bob_9", "a-b", "张三丰", "小明同学", "user12345", "A" * 50],
)
async def test_legal_usernames_are_still_accepted(client, username):
    """对照组：合法用户名一个都不能误杀。

    只测「非法的被拒」是单侧断言 —— 万一 pattern 写成了 `^[a-z]+$`，
    上面那些用例照样全绿，而中文名和大写名全被打回。
    产品面向中文用户，中文名必须能注册。
    """
    r = await client.post("/auth/register", json={"username": username, "password": "secret123"})
    assert r.status_code == 201, (
        f"合法用户名 {username!r} 被拒（{r.status_code}）：{r.text[:200]}"
    )


async def test_length_bounds_are_still_enforced(client):
    """原有的长度约束不能被新的格式校验顺手弄丢。"""
    too_short = await client.post("/auth/register", json={"username": "ab", "password": "secret123"})
    assert too_short.status_code == 422, "2 个字符应该被拒"

    too_long = await client.post(
        "/auth/register", json={"username": "a" * 51, "password": "secret123"}
    )
    assert too_long.status_code == 422, "51 个字符应该被拒"
