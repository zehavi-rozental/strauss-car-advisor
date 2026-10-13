"""בדיקת קטלוג שטראוס מול המאגר של רשות המסים.

הרצה:  python -m app.modules.vehicles.load_catalog

לכל רכב בקטלוג: האם נמצא במאגר לפי המפתח המלא, והאם שווי השימוש בקטלוג
זהה לשווי במאגר. פער = עצירה, כי החישוב חייב לרוץ על נתון אחד ואמין.
"""
from __future__ import annotations

import sys

from app.modules.rules.public import CatalogCar, RulesSnapshot, load_snapshot
from app.shared.db import session_factory
from app.shared.settings import get_settings

from .repository import VehicleKey, VehicleRepository

TAX_YEAR = 2026


def verify_catalog(snapshot: RulesSnapshot, repo: VehicleRepository) -> list[str]:
    problems = []
    for car in snapshot.catalog:
        key = VehicleKey(TAX_YEAR, car.reg_year, car.vehicle_type, car.manufacturer_code, car.model_code)
        info = repo.get(key)
        if info is None:
            problems.append(f"{car.catalog_code} {car.label}: לא נמצא במאגר לפי {key}")
        elif info.use_value != car.use_value:
            problems.append(f"{car.catalog_code} {car.label}: שווי בקטלוג {car.use_value} ≠ במאגר {info.use_value}")
        elif info.drive != car.drive.value:
            problems.append(f"{car.catalog_code} {car.label}: הנעה בקטלוג {car.drive.value} ≠ במאגר {info.drive}")
    return problems


def main() -> None:
    settings = get_settings()
    snapshot = load_snapshot(settings.config_dir)
    problems = verify_catalog(snapshot, VehicleRepository(session_factory()()))
    if problems:
        print("נמצאו פערים בין הקטלוג למאגר:")
        print("\n".join(f"  ✗ {p}" for p in problems))
        sys.exit(1)
    print(f"✓ כל {len(snapshot.catalog)} רכבי הקטלוג נמצאו במאגר עם שווי שימוש והנעה זהים")


if __name__ == "__main__":
    main()
