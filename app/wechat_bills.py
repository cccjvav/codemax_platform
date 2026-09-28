"""Read-only ordinary-merchant ALL bills: authenticated metadata, hash-bound bytes, strict format.

No uploaded files, arbitrary download URLs, decompression, settlement or refund inference.
The 2026 modern bill schema is intentionally exact; a new provider format needs review.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlsplit

import httpx

if TYPE_CHECKING:
    from .wechat_pay import PayConfig

BILL_ZONE = timezone(timedelta(hours=8))
MAX_BYTES = 4 * 1024 * 1024
MAX_ROWS = 5000
DEADLINE = 45
DOWNLOAD_PATHS = ('/v3/billdownload/file', '/v3/bill/downloadurl')
DETAIL_HEADER = ('交易时间', '公众账号ID', '商户号', '特约商户号', '设备号', '微信订单号', '商户订单号', '用户标识', '交易类型', '交易状态', '付款银行', '货币种类', '应结订单金额', '代金券金额', '微信退款单号', '商户退款单号', '退款金额', '充值券退款金额', '退款类型', '退款状态', '商品名称', '商户数据包', '手续费', '费率', '订单金额', '申请退款金额', '费率备注')
TOTAL_HEADER = ('总交易单数', '应结订单总金额', '退款总金额', '充值券退款总金额', '手续费总金额', '订单总金额', '申请退款总金额')
# 逐行累加、再与尾部汇总比对的六个金额列，顺序与 TOTAL_HEADER[1:] 一一对应（手续费可为负）。
# 列号按表头名取（TD-311）；测试用 TOTAL_COLUMNS 独立求和，名字与取值都不变。
AMOUNT_NAMES = ('应结订单金额', '退款金额', '充值券退款金额', '手续费', '订单金额', '申请退款金额')
TOTAL_COLUMNS = tuple(DETAIL_HEADER.index(name) for name in AMOUNT_NAMES)


class _Col:
    """明细行里用到的列号，一律按表头名取（TD-311）：读校验条件时不必对着 27 列表头数位置；
    表头若被改动，名字对不上会在导入时直接抛 ValueError，不会悄悄错位。"""
    TIME = DETAIL_HEADER.index('交易时间')
    APPID = DETAIL_HEADER.index('公众账号ID')
    MCHID = DETAIL_HEADER.index('商户号')
    SUB_MCHID = DETAIL_HEADER.index('特约商户号')
    TRANSACTION_ID = DETAIL_HEADER.index('微信订单号')
    ORDER_NO = DETAIL_HEADER.index('商户订单号')
    TRADE_TYPE = DETAIL_HEADER.index('交易类型')
    STATE = DETAIL_HEADER.index('交易状态')
    CURRENCY = DETAIL_HEADER.index('货币种类')
    COUPON = DETAIL_HEADER.index('代金券金额')
    REFUND_ID = DETAIL_HEADER.index('微信退款单号')
    OUT_REFUND_NO = DETAIL_HEADER.index('商户退款单号')
    REFUND_TYPE = DETAIL_HEADER.index('退款类型')
    REFUND_STATE = DETAIL_HEADER.index('退款状态')
    FEE = DETAIL_HEADER.index('手续费')


class BillError(Exception):
    """Safe fixed diagnostic: never include raw bill cells, URLs, keys or provider messages."""


class _HideBillURL(logging.Filter):
    """HTTPX normally logs the complete URL at INFO, including the five-minute bearer token."""

    def filter(self, record):
        if any(path in record.getMessage() for path in DOWNLOAD_PATHS):
            record.msg, record.args = 'WeChat bill download HTTP exchange (URL redacted)', ()
        return True


# Permanent narrow filter, not a temporary global log-level change racing other requests.
logging.getLogger('httpx').addFilter(_HideBillURL())


@dataclass(frozen=True)
class BillRow:
    line: int
    at: datetime
    appid: str
    transaction_id: str
    order_no: str
    trade_type: str
    state: str
    currency: str
    total: int
    refund_id: str
    out_refund_no: str
    refund_total: int
    refund_state: str


@dataclass(frozen=True)
class Bill:
    day: date
    merchant_id: str
    sha256: str
    rows: tuple[BillRow, ...]
    totals: tuple[int, ...]


def bill_day(value: str, *, now: datetime | None = None) -> date:
    """Explicit past Beijing day, conservative 90-day window; independent of operator timezone."""
    try:
        if not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value, flags=re.ASCII):
            raise ValueError
        day = date.fromisoformat(value)
        today = (now or datetime.now(timezone.utc)).astimezone(BILL_ZONE).date()
        if not 1 <= (today - day).days <= 90:
            raise ValueError
        return day
    except (ValueError, TypeError):
        raise BillError('账单日期须为北京时间过去1至90天的YYYY-MM-DD') from None


def download_path(value) -> str:
    """Pin the two documented paths on the primary TLS host before signing; token stays opaque."""
    if (not isinstance(value, str) or not 1 <= len(value) <= 2048
            or any(ord(c) < 33 or ord(c) > 126 for c in value) or '\\' in value):
        raise BillError('账单下载地址不受信任')
    try:
        url = urlsplit(value)
        query = parse_qsl(url.query, keep_blank_values=True, strict_parsing=True)
        if (url.scheme != 'https' or url.netloc != 'api.mch.weixin.qq.com'
                or url.path not in DOWNLOAD_PATHS or url.fragment or len(query) != 1
                or query[0][0] != 'token' or not query[0][1].strip()
                or any(ord(c) < 32 or ord(c) > 126 for c in query[0][1])
                or not re.fullmatch(r'[A-Za-z0-9._~%+\-/=]+', url.query.removeprefix('token='))
                or not url.query.startswith('token=') or re.search(r'%(?![0-9a-fA-F]{2})', url.query)):
            raise ValueError
        return httpx.URL(value).raw_path.decode('ascii')
    except (ValueError, UnicodeError, httpx.InvalidURL):
        raise BillError('账单下载地址不受信任') from None


async def fetch_bill(cfg: PayConfig, day: date, *, transport=None) -> Bill:
    """Two bounded GETs only. No-statement/errors are failures, never authenticated empty days."""
    from . import wechat_pay as pay

    bill_day(day.isoformat())
    try:
        async with asyncio.timeout(DEADLINE):
            metadata = await pay.apply_trade_bill(cfg, day, transport=transport)
            digest = metadata.get('hash_value')
            if (metadata.get('hash_type') != 'SHA1' or not isinstance(digest, str)
                    or not re.fullmatch(r'[0-9a-fA-F]{40}', digest)):
                raise BillError('账单哈希元数据无效')
            path = download_path(metadata.get('download_url'))
            headers = {'Authorization': pay.auth_header(cfg, 'GET', path, ''),
                       'Accept': 'text/plain', 'Accept-Encoding': 'identity'}
            raw = bytearray()
            async with (
                httpx.AsyncClient(base_url=pay.BASE_URL, transport=transport, timeout=pay.TIMEOUT,
                                  follow_redirects=False, trust_env=False) as client,
                client.stream('GET', path, headers=headers) as response,
            ):
                if response.status_code != 200 or response.headers.get('Content-Encoding', 'identity').lower() != 'identity':
                    raise BillError('账单文件HTTP状态或编码无效；没有生成报告')
                length = response.headers.get('Content-Length')
                if length is not None and (not re.fullmatch(r'[0-9]{1,10}', length) or int(length) > MAX_BYTES):
                    raise BillError('账单文件大小无效或超过4MiB')
                async for chunk in response.aiter_bytes():
                    if len(raw) + len(chunk) > MAX_BYTES:
                        raise BillError('账单文件超过4MiB')
                    raw.extend(chunk)
                if length is not None and int(length) != len(raw):
                    raise BillError('账单文件被截断或长度不符')
            # This endpoint has NO response signature. SHA1 is the official signed metadata contract.
            if not hmac.compare_digest(hashlib.sha1(raw).hexdigest(), digest.lower()):
                raise BillError('账单文件哈希不符；没有生成报告')
            return parse_bill(bytes(raw), day=day, merchant_id=cfg.mchid)
    except (pay.WeChatPayError, httpx.HTTPError, TimeoutError, ValueError, UnicodeError):
        raise BillError('账单申请/下载未获可信结果；稍后显式重试，不得当作零交易') from None


def _money(value: str, *, signed=False) -> int:
    """Yuan to integer cents, never float; only fee fields may be negative."""
    if not re.fullmatch(r'-?[0-9]{1,12}\.[0-9]{2}' if signed else r'[0-9]{1,12}\.[0-9]{2}', value):
        raise BillError('账单金额格式无效')
    whole, fraction = value.lstrip('-').split('.')
    return (int(whole) * 100 + int(fraction)) * (-1 if value.startswith('-') else 1)


def _cells(line: str, size: int) -> list[str]:
    """Official backtick-prefixed comma format, not permissive Excel/RFC quoted CSV."""
    cells = line.split(',')
    if len(cells) != size or any(not cell.startswith('`') or len(cell) > 4096 for cell in cells):
        raise BillError('账单列数、前缀或字段长度无效')
    return [cell[1:] for cell in cells]


def parse_bill(raw: bytes, *, day: date, merchant_id: str) -> Bill:
    """Pure strict parser; callers must authenticate original bytes before treating it as channel data.

    Validate all merchant rows and footer, including other apps/types; discard payer/text fields.
    Refund initiation snapshots remain observations. Aggregate settlement != order contract amount.
    """
    if not raw or len(raw) > MAX_BYTES:
        raise BillError('账单为空或超过4MiB')
    try:
        text = raw.decode('utf-8-sig').replace('\r\n', '\n')
    except UnicodeError:
        raise BillError('账单不是UTF-8') from None
    if any(ord(c) < 32 and c != '\n' for c in text):
        raise BillError('账单含未转义控制字符')
    lines = text.removesuffix('\n').split('\n')
    if (not 3 <= len(lines) <= MAX_ROWS + 3 or tuple(lines[0].split(',')) != DETAIL_HEADER
            or tuple(lines[-2].split(',')) != TOTAL_HEADER):
        raise BillError('账单表头、尾部或5000行预算不符；不接受旧版/未知格式')
    totals = [0] * 6
    rows, seen = [], set()
    for number, line in enumerate(lines[1:-2], 2):
        cells = _cells(line, len(DETAIL_HEADER))
        try:
            at = datetime.strptime(cells[_Col.TIME], '%Y-%m-%d %H:%M:%S').replace(tzinfo=BILL_ZONE)
            if at.strftime('%Y-%m-%d %H:%M:%S') != cells[_Col.TIME] or at.date() != day:
                raise ValueError
        except ValueError:
            raise BillError('账单交易日期无效或不属于指定日') from None
        if cells[_Col.MCHID] != merchant_id or cells[_Col.SUB_MCHID] != '0':
            raise BillError('账单商户或普通直连商户身份不符')
        state = cells[_Col.STATE]
        if state not in ('SUCCESS', 'REFUND', 'REVOKED'):
            raise BillError('账单交易状态未知')
        for index in (_Col.APPID, _Col.TRANSACTION_ID, _Col.ORDER_NO):
            if not re.fullmatch(r'[A-Za-z0-9_\-|*@.]{1,64}', cells[index]):
                raise BillError('账单身份字段无效')
        if not re.fullmatch(r'[A-Z]{3}', cells[_Col.CURRENCY]) or not cells[_Col.TRADE_TYPE]:
            raise BillError('账单币种或交易类型无效')
        amounts = [_money(cells[index], signed=index == _Col.FEE) for index in TOTAL_COLUMNS]
        _money(cells[_Col.COUPON])  # Coupon amount is not the contract total.
        for index, amount in enumerate(amounts):
            totals[index] += amount
        settlement, refunded, coupon_refunded, _fee, order_total, refund_applied = amounts
        refund_id, out_refund_no = cells[_Col.REFUND_ID], cells[_Col.OUT_REFUND_NO]
        if state == 'SUCCESS':
            if (order_total <= 0 or refunded or coupon_refunded or refund_applied
                    or (refund_id, out_refund_no) != ('0', '0') or cells[_Col.REFUND_TYPE] or cells[_Col.REFUND_STATE]):
                raise BillError('付款行夹带退款字段或金额无效')
            identities = (('payment_no', cells[_Col.ORDER_NO]), ('payment_id', cells[_Col.TRANSACTION_ID]))
        else:
            if settlement or order_total or refund_applied <= 0:
                raise BillError('退款/撤销行的订单金额或申请退款金额无效')
            for value in (refund_id, out_refund_no):
                if not re.fullmatch(r'[A-Za-z0-9_\-|*@.]{1,64}', value):
                    raise BillError('账单退款身份字段无效')
            if cells[_Col.REFUND_STATE] not in ('SUCCESS', 'PROCESSING', 'FAIL', 'CHANGE') or not cells[_Col.REFUND_TYPE]:
                raise BillError('账单退款快照状态无效')
            identities = (('refund_no', out_refund_no), ('refund_id', refund_id))
        if any(identity in seen for identity in identities):
            raise BillError('账单包含重复付款或退款标识')
        seen.update(identities)
        rows.append(BillRow(number, at, cells[_Col.APPID], cells[_Col.TRANSACTION_ID], cells[_Col.ORDER_NO],
                            cells[_Col.TRADE_TYPE], state, cells[_Col.CURRENCY], order_total,
                            refund_id, out_refund_no, refund_applied, cells[_Col.REFUND_STATE]))
    footer = _cells(lines[-1], len(TOTAL_HEADER))
    if not re.fullmatch(r'[0-9]{1,8}', footer[0]):
        raise BillError('账单总笔数无效')
    declared = [_money(value, signed=index == 3) for index, value in enumerate(footer[1:])]
    if int(footer[0]) != len(rows) or declared != totals:
        raise BillError('账单总笔数或金额汇总不符')
    return Bill(day, merchant_id, hashlib.sha256(raw).hexdigest(), tuple(rows), tuple(totals))
