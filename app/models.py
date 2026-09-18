from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
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
    # TD-138：管理员标记（0 普通 / 1 管理员）。抓取入库端点只对它开放 ——
    # 公开端点等于给任何人一个「让服务器抓任意 URL + 烧 LLM token」的入口。
    #
    # 角色**不进 JWT**：`get_current_user` 每个请求都从库里读用户，所以改角色
    # 立刻生效，不必等 token 过期，也不必像 TD-70 那样再造一个失效时间戳。
    # 代价是每请求一次查库 —— 这个查本来就要做（要读 status 和 password_changed_at）。
    role: Mapped[int] = mapped_column(SmallInteger, default=0)
    # 最近一次改密码的时刻（TD-70）。JWT 里带这个时间戳的副本，校验时对不上就拒 ——
    # 这样改密码能一次吊销该用户**所有**旧 token，不必维护 jti 黑名单表。
    # 为 None 表示从未改过密码（注册时建的号）。
    credential_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    password_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class Order(Base):
    """订单表；主路径 pending -> paid -> downloaded，也允许 pending -> closed -> paid。"""

    __tablename__ = "sys_order"

    id: Mapped[int] = mapped_column(primary_key=True)
    order_no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    product_name: Mapped[str] = mapped_column(String(100))
    amount: Mapped[int] = mapped_column(Integer)  # 金额，单位：分
    # NULL on historical orders: never infer their payment/delivery contract from today's settings.
    payment_mode: Mapped[str | None] = mapped_column(String(16))
    merchant_id: Mapped[str | None] = mapped_column(String(64))
    app_id: Mapped[str | None] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(3), default="CNY", server_default="CNY")
    delivery_key: Mapped[str | None] = mapped_column(String(512))
    delivery_digest: Mapped[str | None] = mapped_column(String(64))
    delivery_size: Mapped[int | None] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    code_url: Mapped[str | None] = mapped_column(String(512))  # NATIVE 下单返回的二维码链接
    transaction_id: Mapped[str | None] = mapped_column(String(64))  # 微信支付订单号（回调解出）
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        # **同一用户同一时刻最多一张待支付单**（TD-199）。
        # 必须由数据库兜底而不是在应用层加锁：多实例部署时进程内锁各算各的
        # （与限流的 TD-141 同一个道理），而「先查 pending 再新建」在 asyncio
        # 交错下必然漏 —— 5 个并发请求的 SELECT 会全部跑完才开始 INSERT。
        # 用**部分**唯一索引而不是普通唯一约束：paid / closed / downloaded 的历史单
        # 必须能有多张，只有 pending 这一种状态需要唯一。
        # SQLite 3.8+ 与 PostgreSQL 都支持部分索引，两个后端口径一致。
        Index(
            "uq_sys_order_user_pending",
            "user_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
    )


class Article(Base):
    """抓取入库的文章（S4-01-4）。

    `url` 唯一 —— 同一篇不重复入库；`published_at` 按源站原文存字符串，
    各家日期格式差异太大，强行解析成 datetime 反而会丢信息（TD-137）。
    """

    __tablename__ = "sys_article"

    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(500), unique=True)
    title: Mapped[str] = mapped_column(String(300))
    author: Mapped[str | None] = mapped_column(String(100))
    published_at: Mapped[str | None] = mapped_column(String(50))
    content: Mapped[str] = mapped_column(Text)
    source_site: Mapped[str | None] = mapped_column(String(200))  # 域名，便于按站分组
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SysConfig(Base):
    """系统配置键值表。"""

    __tablename__ = "sys_config"

    id: Mapped[int] = mapped_column(primary_key=True)
    config_key: Mapped[str] = mapped_column(String(64), unique=True)
    config_value: Mapped[str] = mapped_column(Text)
    remark: Mapped[str | None] = mapped_column(String(255))


class SysDiagram(Base):
    """用户的 Drawio 流程图（S2-01-3）：content 存 drawio XML，属用户私有资产。"""

    __tablename__ = "sys_diagram"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    name: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text)
    # 软删除（TD-64）：NULL = 存活，非 NULL = 删除时刻。删除只打时间戳，用户可以自己恢复。
    # 默认活跃列表和普通详情需过滤 deleted_at；回收站、恢复和永久删除必须查询已删除行，
    # 由路由按操作选择 _alive/_owned 参数，不能给所有读取无条件添加同一过滤。
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 乐观锁版本号（TD-65）：每次保存 +1。客户端把它当 ETag 拿着，保存时用
    # If-Match 带回来；服务端用**原子 CAS**（UPDATE ... WHERE version=?）判定，
    # 不是「先读出来比一比再写」—— 后者在并发下两边都会读到同一个版本、都通过
    # 检查、都写进去，丢更新照旧（与 TD-158 下载端点是同一个坑）。
    version: Mapped[int] = mapped_column(Integer, default=1)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    update_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class OAuthClient(Base):
    """SSO客户端；生产额外限制为显式信任的自有站点，不是第三方最小权限授权。"""

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
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    credential_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    used: Mapped[bool] = mapped_column(Boolean, default=False)
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportMessage(Base):
    """One persistent customer/admin conversation per customer; nonce deduplicates retries."""

    __tablename__ = "support_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    sender_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    sender_role: Mapped[int] = mapped_column(SmallInteger)
    body: Mapped[str] = mapped_column(Text)
    client_nonce: Mapped[str] = mapped_column(String(36))
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("sender_id", "client_nonce", name="uq_support_sender_nonce"),
        Index("idx_support_customer_id", "customer_id", "id"),
    )


class SchemaMigration(Base):
    """Applied SQL checksum ledger; only the offline maintenance CLI writes this table."""

    __tablename__ = "schema_migration"

    version: Mapped[str] = mapped_column(String(4), primary_key=True)
    checksum: Mapped[str] = mapped_column(String(64))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PaymentReceipt(Base):
    """One accepted settlement per order; source + transaction reference has exactly one owner.

    Application append-only, not a tamper-proof financial accounting system. Historical paid
    orders are not retroactively asserted to have verified receipts by the migration.
    """
    __tablename__ = "payment_receipt"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sys_order.id"), unique=True)
    source: Mapped[str] = mapped_column(String(16))
    transaction_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    merchant_id: Mapped[str | None] = mapped_column(String(64))
    app_id: Mapped[str | None] = mapped_column(String(64))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    actor_name: Mapped[str | None] = mapped_column(String(50))
    evidence: Mapped[str | None] = mapped_column(String(500))
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("source", "transaction_id", name="uq_payment_source_transaction"),
        CheckConstraint("amount > 0 AND currency = 'CNY'", name="ck_receipt_amount_currency"),
        CheckConstraint("source IN ('wechat', 'manual', 'mock')", name="ck_receipt_source"),
        CheckConstraint("source != 'manual' OR (actor_id IS NOT NULL AND actor_name IS NOT NULL AND evidence IS NOT NULL)",
                        name="ck_receipt_manual_evidence"),
    )


class PaymentEvent(Base):
    """Append-only application journal: prepay started/ready/unknown and explicit legacy bindings.

    No raw provider body, payer identifiers or secrets. A started attempt without a result is
    unknown, not proof of failure. No external reconciliation/refund is implied by this journal.
    """
    __tablename__ = "payment_event"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sys_order.id"))
    attempt_id: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(32))
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("sys_user.id"))
    actor_name: Mapped[str | None] = mapped_column(String(50))
    evidence: Mapped[str | None] = mapped_column(String(500))
    create_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("attempt_id", "kind", name="uq_payment_attempt_kind"),
        Index("idx_payment_event_order", "order_id", "id"),
    )


class RefundReceipt(Base):
    """Append-only evidence of ONE completed full refund; observations live in PaymentEvent.

    Original payment/status stays intact. Existence revokes this order's future download grants.
    No partial refunds, mock refunds, outgoing money API or service cancellation is implied.
    """
    __tablename__ = "refund_receipt"
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("sys_order.id"), unique=True)
    payment_receipt_id: Mapped[int] = mapped_column(ForeignKey("payment_receipt.id"), unique=True)
    source: Mapped[str] = mapped_column(String(16))
    merchant_id: Mapped[str] = mapped_column(String(64))
    refund_id: Mapped[str] = mapped_column(String(64))
    out_refund_no: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    actor_id: Mapped[int] = mapped_column(ForeignKey("sys_user.id"))
    actor_name: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[str] = mapped_column(String(500))
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint("source", "refund_id", name="uq_refund_source_id"),
        UniqueConstraint("source", "merchant_id", "out_refund_no", name="uq_refund_merchant_reference"),
        CheckConstraint("amount > 0 AND currency = 'CNY'", name="ck_refund_amount_currency"),
        CheckConstraint("source IN ('wechat', 'manual')", name="ck_refund_source"),
    )


class RefundRequest(Base):
    """Immutable FULL WeChat refund preparation, not submission authorization or completion.

    One per original payment/order. Never auto-send old preparations when adding a sender later.
    The stable provider reference survives lost ACKs; no delete-and-recreate/correction API.
    """
    __tablename__ = 'refund_request'
    id: Mapped[int] = mapped_column(primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sys_order.id'), unique=True)
    payment_receipt_id: Mapped[int] = mapped_column(ForeignKey('payment_receipt.id'), unique=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True)
    out_refund_no: Mapped[str] = mapped_column(String(64))
    merchant_id: Mapped[str] = mapped_column(String(64))
    app_id: Mapped[str] = mapped_column(String(64))
    amount: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    actor_id: Mapped[int] = mapped_column(ForeignKey('sys_user.id'))
    actor_name: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        UniqueConstraint('merchant_id', 'out_refund_no', name='uq_request_merchant_reference'),
        CheckConstraint("amount > 0 AND currency = 'CNY'", name='ck_request_amount_currency'),
    )


class RefundAuthorization(Base):
    """Explicit immutable outbound contract, never a queue consumed automatically."""
    __tablename__ = 'refund_authorization'
    id: Mapped[int] = mapped_column(primary_key=True)
    preparation_id: Mapped[int] = mapped_column(ForeignKey('refund_request.id'), unique=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('sys_user.id'))
    actor_name: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    digest: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundSendStop(Base):
    """Permanent local stop; never claims a provider cancellation or changes the frozen request."""
    __tablename__ = 'refund_send_stop'
    id: Mapped[int] = mapped_column(primary_key=True)
    authorization_id: Mapped[int] = mapped_column(ForeignKey('refund_authorization.id'), unique=True)
    request_id: Mapped[str] = mapped_column(String(32), unique=True)
    actor_id: Mapped[int] = mapped_column(ForeignKey('sys_user.id'))
    actor_name: Mapped[str] = mapped_column(String(50))
    evidence: Mapped[str] = mapped_column(String(160))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class RefundVerificationJob(Base):
    """Durable read-only follow-up per verified notice; lease tokens fence stale workers."""
    __tablename__ = 'refund_verification_job'
    id: Mapped[int] = mapped_column(primary_key=True)
    notice_event_id: Mapped[int] = mapped_column(ForeignKey('payment_event.id'), unique=True)
    order_id: Mapped[int] = mapped_column(ForeignKey('sys_order.id'))
    state: Mapped[str] = mapped_column(String(16), server_default='pending')
    attempts: Mapped[int] = mapped_column(Integer, server_default='0')
    token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    outcome: Mapped[str] = mapped_column(String(32), server_default='queued')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    __table_args__ = (
        CheckConstraint("state IN ('pending','running','retry','verified','attention')", name='ck_verify_state'),
        CheckConstraint('attempts >= 0 AND attempts <= 8', name='ck_verify_attempts'),
        CheckConstraint("(state = 'running' AND token IS NOT NULL AND lease_until IS NOT NULL) OR "
                        "(state <> 'running' AND token IS NULL AND lease_until IS NULL)", name='ck_verify_lease'),
        Index('ix_verify_due', 'state', 'next_at'),
    )
