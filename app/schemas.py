from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=64)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str | None
    avatar: str | None


class PasswordChangeIn(BaseModel):
    """改密码入参。新密码规则与 RegisterIn 保持一致，避免两套标准。"""

    old_password: str = Field(min_length=1, max_length=64)
    new_password: str = Field(min_length=6, max_length=64)


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
