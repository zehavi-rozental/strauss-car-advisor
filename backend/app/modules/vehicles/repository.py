from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .models import TaxVehicle


@dataclass(frozen=True)
class VehicleKey:
    tax_year: int
    reg_year: int
    vehicle_type: str
    manufacturer_code: str
    model_code: str


@dataclass(frozen=True)
class VehicleInfo:
    key: VehicleKey
    manufacturer_name: str
    commercial_name: str
    model_description: str
    drive: str
    fuel_code: str
    use_value: Decimal

    @property
    def label(self) -> str:
        return f"{self.commercial_name} ({self.key.reg_year})"


def _info(v: TaxVehicle) -> VehicleInfo:
    return VehicleInfo(
        key=VehicleKey(v.tax_year, v.reg_year, v.vehicle_type, v.manufacturer_code, v.model_code),
        manufacturer_name=v.manufacturer_name, commercial_name=v.commercial_name,
        model_description=v.model_description, drive=v.drive, fuel_code=v.fuel_code,
        use_value=v.use_value,
    )


class VehicleRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, key: VehicleKey) -> Optional[VehicleInfo]:
        row = self.session.get(TaxVehicle, (key.tax_year, key.reg_year, key.vehicle_type,
                                            key.manufacturer_code, key.model_code))
        return None if row is None else _info(row)

    def search(self, text: str, reg_year: Optional[int] = None, tax_year: int = 2026,
               limit: int = 50) -> list[VehicleInfo]:
        """חיפוש לפי כינוי מסחרי (לרכב הנוכחי של העובד)."""
        words = [w for w in re.split(r"\s+", text.upper().strip()) if w]
        if not words:
            return []
        stmt = select(TaxVehicle).where(TaxVehicle.tax_year == tax_year)
        for w in words:
            stmt = stmt.where(func.upper(TaxVehicle.commercial_name).contains(w))
        if reg_year is not None:
            stmt = stmt.where(TaxVehicle.reg_year == reg_year)
        stmt = stmt.order_by(TaxVehicle.commercial_name, TaxVehicle.reg_year.desc()).limit(limit)
        return [_info(v) for v in self.session.scalars(stmt)]
