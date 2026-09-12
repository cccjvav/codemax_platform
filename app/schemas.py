import re
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 用户名白名单：看得见的字母/数字/下划线（`\w` 在 Python 的 str 模式下含中文），
# 外加连字符。用**白名单**而不是把 `<>&"'` 逐个拉黑 —— 黑名单永远漏，
# 而这里能列举的合法字符本来就只有这几类。
_USERNAME_RE = re.compile(r"^[\w-]+$", re.UNICODE)

# 保留名（比较时统一转小写）。种子管理员就叫 `admin`
# （`database init/full_init.sql` 第 68 行），而登录是精确匹配、
# PostgreSQL 的 VARCHAR `=` 区分大小写 ⇒ `Admin` 是个独立账号却能注册，
# 唯一用途就是在界面上冒充管理员。
_RESERVED_USERNAMES = frozenset({"admin", "administrator", "root", "system"})

# bcrypt 的输入上限是 **72 字节**（不是 72 个字符），超出部分**静默丢弃**、不报错。
# 实测：`verify("密"*24, hash("密"*64))` 返回 True —— 两个完全不同的密码被当成同一个。
# 而下面的 `max_length=64` 卡的是**字符数**：一个 64 汉字的密码是 192 字节，
# 照样能通过。所以必须额外卡一道字节数。
_BCRYPT_MAX_BYTES = 72


def _check_password_bytes(v: str) -> str:
    """拒绝 UTF-8 编码后超过 72 字节的密码。

    为什么要拒绝而不能让 bcrypt 截断：截断之后**两个不同的密码可能通过同一校验**。
    受害者注册了 64 个汉字的密码，攻击者只要知道前 24 个汉字就能进他的账号 ——
    而「密码前缀」恰恰是最容易被猜到的部分。

    为什么不在 bcrypt 之前先做 SHA-256 预哈希（那样就没有长度上限了）：
    那会改变哈希格式，库里已有的哈希（含 `full_init.sql` 的种子管理员）全部失效。
    而拒绝的代价很小 —— 只影响「24 个汉字以上」的密码，正常使用碰不到，
    而且 `max_length=64` 本来就已经设了字符上限。
    """
    if "\x00" in v:
        raise ValueError("密码不能包含空字符")
    n = len(v.encode("utf-8"))
    if n > _BCRYPT_MAX_BYTES:
        raise ValueError(f"密码编码后为 {n} 字节，超过 bcrypt 的 72 字节上限，请缩短")
    return v


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=64)

    _password_bytes = field_validator("password")(_check_password_bytes)

    @field_validator("username")
    @classmethod
    def _check_username(cls, v: str) -> str:
        """格式 + 保留名。

        为什么要在 schema 层挡而不是在路由里判：
          · 422 由 FastAPI 自动生成，错误信息结构化、前端能直接展示；
          · 所有走 `RegisterIn` 的入口（现在只有 `/auth/register`）自动一致，
            不会因为将来多一个注册入口而漏掉。

        为什么这三类字符必须挡（不是为了防 SQL 注入 —— 全站参数化查询，
        注入本来就打不进去）：
          ① **可辨识性**：`'   '` 与 `'  bob  '` 在管理端列表里与正常账号
             无法区分，出了钱货纠纷查不到人。
          ② **上下文安全**：用户名会进 HTML（OAuth 同意页）、JSON 响应、
             日志与导出文件。Jinja 的 autoescape 只保得住 HTML 那一处。
          ③ **日志完整性**：`\n` 能让攻击者在日志里伪造整行记录。
        """
        if not _USERNAME_RE.fullmatch(v):
            raise ValueError("用户名只能包含字母、数字、下划线、连字符或中文，不能有空格与特殊符号")
        if v.lower() in _RESERVED_USERNAMES:
            raise ValueError("该用户名为系统保留名，请换一个")
        return v


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str | None
    avatar: str | None
    role: int


class PasswordChangeIn(BaseModel):
    """改密码入参。新密码规则与 RegisterIn 保持一致，避免两套标准。"""

    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)

    # 与 RegisterIn 同一条 72 字节规则 —— 否则绕过注册就能塞进超长密码，
    # 本类 docstring 里「新密码规则与 RegisterIn 保持一致」那句话也就成了空话。
    _password_bytes = field_validator("new_password")(_check_password_bytes)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ErDiagramIn(BaseModel):
    ddl: str = Field(min_length=1, max_length=20000)


class MermaidIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class ArticleIngestIn(BaseModel):
    """抓取入库入参（TD-138，仅管理员）。

    这里**只用长度**卡 url，不做格式校验：真正的校验是
    `crawler.assert_public_url`（协议白名单 + 逐个解析结果必须 `is_global`）。
    在 schema 里再写一套 URL 规则等于两处真相，SSRF 判定必须只有一处。
    500 与 `sys_article.url VARCHAR(500)` 对齐，超长直接在入口挡掉。
    """

    url: str = Field(min_length=1, max_length=500)
    # dynamic 字段保留兼容；当前动态浏览器链路因缺少网络隔离而停用，True 不代表可用。
    # 默认 False 走受控静态抓取；安装浏览器包不会解除 _goto 的 fail-closed 边界。
    dynamic: bool = False


class SupportIn(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class DiagramIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=500000)  # drawio XML 可能较大


class DiagramSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    update_time: datetime
    # 乐观锁版本号（TD-65）。响应里另有一个 ETag 头是同一个值；这里也放一份，
    # 是因为客户端不总能方便地读响应头，而列表页也需要知道每张图的当前版本。
    version: int


class DiagramOut(DiagramSummary):
    content: str
