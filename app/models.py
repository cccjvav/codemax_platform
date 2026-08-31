from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class User(Base):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(50), unique=True)
    password: Mapped[str] = mapped_column(String(255))
    nickname: Mapped[str | None] = mapped_column(String(50))
    avatar: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(SmallInteger, default=1)  # 1正常 0禁用
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class Order(Base):
    """订单表，状态机：pending -> paid -> downloaded（阶段三实现支付流程）。"""

    __tablename__ = "sys_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    product_name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)  # 金额，单位：分
    status: Mapped[str] = mapped_column(String(20), default="pending")
    paid_at: Mapped[datetime | None] = mapped_column(DateTime)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())


class SysConfig(Base):
    """系统配置键值表。"""

    __tablename__ = "sys_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_key: Mapped[str] = mapped_column(String(64), unique=True)
    config_value: Mapped[str] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(String(255))


class OAuthClient(Base):
    """OAuth2 客户端（接入 SSO 的第三方应用/平台）。"""

    __tablename__ = "oauth_client"

    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[str] = mapped_column(String(64), unique=True)
    client_secret_hash: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100))
    redirect_uri: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(SmallInteger, default=1)


class OAuthCode(Base):
    """一次性授权码，绑定用户与客户端，短时有效。"""

    __tablename__ = "oauth_code"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    client_id: Mapped[int] = mapped_column(ForeignKey("oauth_client.id"))
    redirect_uri: Mapped[str] = mapped_column(String(255))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
