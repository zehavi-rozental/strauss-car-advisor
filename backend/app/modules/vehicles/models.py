"""טבלת שווי השימוש מהמאגר של רשות המסים."""
from __future__ import annotations

from decimal import Decimal
from typing import Optional

from sqlalchemy import Boolean, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.shared.db import Base, Money


class TaxVehicle(Base):
    """שורה במאגר. המפתח הרשמי: שנת מס, שנת רישום, סוג רכב, קוד תוצר, קוד דגם.

    סוג הרכב הכרחי במפתח: ATTO 3 ו-TANG חולקים קוד תוצר+דגם.
    """

    __tablename__ = "vehicles_tax_values"

    tax_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    reg_year: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_type: Mapped[str] = mapped_column(String(2), primary_key=True)
    manufacturer_code: Mapped[str] = mapped_column(String(5), primary_key=True)  # עם אפסים מובילים
    model_code: Mapped[str] = mapped_column(String(5), primary_key=True)

    manufacturer_name: Mapped[str] = mapped_column(String(64))
    model_description: Mapped[str] = mapped_column(String(64))
    commercial_name: Mapped[str] = mapped_column(String(64), index=True)
    fuel_code: Mapped[str] = mapped_column(String(2))
    drive: Mapped[str] = mapped_column(String(10))           # PETROL / HYBRID / PLUG_IN / ELECTRIC
    automatic: Mapped[bool] = mapped_column(Boolean)
    list_price: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    use_value: Mapped[Decimal] = mapped_column(Money)
    effective_date: Mapped[str] = mapped_column(String(10))
