"""TD-293：人工填写的自由文本字段统一拒绝 C0 / DEL / C1 控制字符。"""
from __future__ import annotations

from typing import Annotated

import pytest
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.routers import payments_admin, refunds_admin, shop
from app.routers.shop import NO_CONTROL_CHARS


def _free_text_fields() -> list[tuple[str, str, object]]:
    """三个路由模块里所有请求模型中、pattern 是「排除控制字符」类的字段：(模型名, 字段名, FieldInfo)。"""
    found = []
    for module in (shop, payments_admin, refunds_admin):
        for name, obj in vars(module).items():
            if not (isinstance(obj, type) and issubclass(obj, BaseModel) and obj.__module__ == module.__name__):
                continue
            for field, info in obj.model_fields.items():
                pattern = next((m.pattern for m in info.metadata if getattr(m, "pattern", None)), None)
                if pattern and pattern.startswith("^[^\\x00"):
                    found.append((name, field, info))
    return found


def test_every_free_text_pattern_is_the_shared_constant():
    fields = _free_text_fields()
    names = {(m, f) for m, f, _ in fields}
    # 已知的九处（含继承链上重新声明的字段）都要在，且都用同一个常量；新增字段也不能写回只拒 C0 的旧正则
    assert {("EvidenceIn", "evidence"), ("ManualReceiptIn", "evidence"), ("LegacyBindingIn", "source_key"),
            ("LegacyBindingIn", "evidence"), ("ReviewIn", "evidence"), ("CloseChannelIn", "evidence"), ("RefundIn", "evidence"),
            ("RefundPrepareIn", "evidence"), ("VerificationControlIn", "evidence")} <= names
    for model, field, info in fields:
        pattern = next(m.pattern for m in info.metadata if getattr(m, "pattern", None))
        assert pattern == NO_CONTROL_CHARS, f"{model}.{field} 仍用 {pattern!r}"


@pytest.mark.parametrize("control", ["\x00", "\x1f", "\x7f", "\x85", "\x9f"])
def test_control_characters_are_rejected_in_every_free_text_field(control):
    for model, field, info in _free_text_fields():
        adapter = TypeAdapter(Annotated[str, info])
        with pytest.raises(ValidationError):
            adapter.validate_python(f"核对{control}流水")
        # 正常中文、数字与不间断空格（\xa0 不是控制字符）仍然通过
        assert adapter.validate_python("核对流水 20260927\xa0已到账") == "核对流水 20260927\xa0已到账", f"{model}.{field}"
