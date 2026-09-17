"""Refund callback ingress: durable verified observations, no automatic money/rights decisions."""
import asyncio

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..payment_ledger import PaymentConflict
from ..refund_notifications import parse_notice, save_notice
from ..wechat_pay import WeChatPayError, pay_config

router = APIRouter(tags=['退款通知'])
NOTIFY_BUDGET = 4.0


def failure(status: int, message: str) -> JSONResponse:
    return JSONResponse({'code': 'FAIL', 'message': message}, status_code=status, headers={'Cache-Control': 'no-store'})


@router.post('/shop/refunds/notify')
async def refund_notify(request: Request, db: AsyncSession = Depends(get_db)):
    """No Cookie/admin auth: verify platform signature and original merchant/receipt instead.

    Application budget < provider's 5s window, not a measured end-to-end SLA. ACK means inbox
    committed, NOT full ORIGINAL refund confirmed. No outbound I/O or ephemeral background task.
    """
    cfg = pay_config()
    if not cfg.notify_ready:
        return failure(503, '退款通知商户/应用/验签配置不完整')
    if request.headers.get('content-encoding', 'identity').lower() != 'identity':
        return failure(415, '退款通知不支持压缩请求')
    try:
        async with asyncio.timeout(NOTIFY_BUDGET):
            raw = bytearray()
            async for chunk in request.stream():
                if len(raw) + len(chunk) > 65536:
                    return failure(413, '退款通知超过接收上限')
                raw.extend(chunk)
            notice = parse_notice(cfg, request.headers, bytes(raw))
            await save_notice(db, cfg, notice)
        return Response(status_code=204, headers={'Cache-Control': 'no-store'})
    except (WeChatPayError, UnicodeError):
        return failure(400, '退款通知验签、解密或协议核验失败')
    except PaymentConflict:
        return failure(409, '退款通知与原订单、收款或已记录通知冲突')
    except (TimeoutError, SQLAlchemyError):
        return failure(503, '退款通知记录结果未知，请按原通知重试')
