"""SheetSpec: one model's Excel layout, derived from the class ClassVar hints.

Honours the convention the topology entities already declare
(``SHEET_NAME``/``TAB_COLOR``/``ORIENTATION``/``HIDE_IN_EXCEL``) plus per-field
``excel`` metadata, so existing models serialize with no change. A field marked
hidden is still written — to a hidden column — so the round trip stays lossless
(hiding is a display concern, never data loss).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .codecs import excel_meta, is_constant_literal

__all__ = ["SheetSpec", "columns_for"]


def columns_for(model_cls: type) -> tuple[list[str], list[str]]:
    """(visible, hidden) column names for ``model_cls`` in field-definition order.

    Single-value ``Literal`` constants are dropped entirely (they are fixed).
    A field is hidden when it is in the class ``HIDE_IN_EXCEL`` list or its
    ``excel`` metadata sets ``hidden: true``."""
    hide = set(getattr(model_cls, "HIDE_IN_EXCEL", ()) or ())
    visible: list[str] = []
    hidden: list[str] = []
    for name, field_info in model_cls.model_fields.items():
        if is_constant_literal(field_info.annotation):
            continue
        if name in hide or excel_meta(field_info).get("hidden"):
            hidden.append(name)
        else:
            visible.append(name)
    return visible, hidden


@dataclass(frozen=True)
class SheetSpec:
    """The resolved Excel layout of one pydantic model."""

    model: type
    sheet_name: str
    orientation: str
    tab_color: str | None
    visible: list[str]
    hidden: list[str]

    @property
    def columns(self) -> list[str]:
        """All written columns — visible first, then hidden (still persisted)."""
        return [*self.visible, *self.hidden]

    @classmethod
    def from_model(cls, model_cls: type) -> "SheetSpec":
        sheet_name = getattr(model_cls, "SHEET_NAME", None)
        if not sheet_name:
            raise ValueError(f"{model_cls.__name__} declares no SHEET_NAME ClassVar; cannot map it to a worksheet")
        visible, hidden = columns_for(model_cls)
        return cls(
            model=model_cls,
            sheet_name=str(sheet_name),
            orientation=str(getattr(model_cls, "ORIENTATION", "HORIZONTAL")),
            tab_color=getattr(model_cls, "TAB_COLOR", None) or None,
            visible=visible,
            hidden=hidden,
        )


def field_description(model_cls: type, name: str) -> Any:
    fi = model_cls.model_fields.get(name)
    return getattr(fi, "description", None) if fi is not None else None
