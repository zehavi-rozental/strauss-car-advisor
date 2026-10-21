"""Money written and read back is the same Decimal, written the same way, on SQLite and on PostgreSQL
(spec section 10). Runs on whichever database the `engine` fixture gives (TEST_DATABASE_URL for PostgreSQL)."""
from __future__ import annotations

from decimal import Decimal

import pytest
from sqlalchemy import select

from app.modules.vehicles.models import TaxVehicle
from app.shared.db import session_factory


@pytest.mark.parametrize("written,read", [("2650", "2650"), ("2650.00", "2650"), ("2650.50", "2650.5"),
                                          ("0.05", "0.05"), ("14800", "14800")])
def test_money_round_trip(engine, written, read):
    with session_factory(engine)() as s, s.begin():
        s.add(TaxVehicle(tax_year=2026, reg_year=2021, vehicle_type="1", manufacturer_code="09999",
                         model_code="00001", manufacturer_name="TEST", model_description="TEST",
                         commercial_name="TEST", fuel_code="1", drive="PETROL", automatic=True, list_price=None,
                         use_value=Decimal(written), effective_date="2026-01-01"))
    with session_factory(engine)() as s:
        value = s.scalars(select(TaxVehicle.use_value).where(TaxVehicle.manufacturer_code == "09999")).one()
    assert isinstance(value, Decimal)
    assert value == Decimal(written) and str(value) == read
