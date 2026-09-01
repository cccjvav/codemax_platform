"""智能客服接口（S4-02）。

刻意**不设鉴权**：售前咨询不该要求先注册，这也是引流入口之一。
代价是任何人都能刷，所以挂 LLM 档限流（比工具档更严，因为每次调用都花钱）。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database import get_db
from ..ratelimit import rate_limit
from ..schemas import SupportIn
from ..tools.support import answer

router = APIRouter(prefix="/support", tags=["智能客服"])


@router.post("/ask", dependencies=[Depends(rate_limit("support", "RATE_LIMIT_LLM"))])
async def ask(data: SupportIn, db: AsyncSession = Depends(get_db)):
    """三层客服总入口：FAQ 秒回 / 闲聊 LLM / 专业问题 RAG，兜底转人工。

    返回体里带 `escalated` 与 `reason`：前端据此决定是否弹「转人工」，
    运维据此排查为什么某类问题总是兜底。
    """
    reply = await answer(data.text, db)
    return {
        "answer": reply.answer,
        "intent": reply.intent.value,
        "confidence": round(reply.confidence, 3),
        "source": reply.source,
        "escalated": reply.escalated,
        "reason": reply.reason,
        "references": list(reply.references),
    }
