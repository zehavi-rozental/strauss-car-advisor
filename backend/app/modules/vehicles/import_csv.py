"""ייבוא המאגר של רשות המסים (CSV, windows-1255).

הרצה:  python -m app.modules.vehicles.import_csv [נתיב]

* טרנזקציה אחת: שגיאה בשורה כלשהי מבטלת הכול והמסד נשאר כמו שהיה.
* אידמפוטנטי: השורות של שנות המס שבקובץ מוחלפות, כך שהרצה חוזרת לא מכפילה.
* מדפיס דוח: נקלטו, כפילויות על המפתח, נדחו והסיבה.
"""
from __future__ import annotations

import csv
import sys
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import delete, insert
from sqlalchemy.orm import Session

from app.shared.db import Base, get_engine, session_factory
from app.shared.settings import get_settings

from .models import TaxVehicle

# כותרת העמודה במאגר ("1-חשמלי.2-היברידי.3-פלג-אין") מטעה. הקידוד בפועל:
DRIVE_CODES = {"0": "PETROL", "1": "HYBRID", "2": "PLUG_IN", "3": "ELECTRIC"}

COLUMNS = {
    "tax_year": "שנת מס",
    "reg_year": "שנת רישום",
    "vehicle_type": "קוד סוג רכב",
    "manufacturer_code": "קוד תוצר",
    "manufacturer_name": "תוצר",
    "model_code": "קוד דגם",
    "model_description": "תאור דגם",
    "fuel_code": "סוג דלק",
    "commercial_name": "כינוי מסחרי",
    "automatic": "לא-0.כן-1 אוטומט",
    "drive": "1-חשמלי.2-היברידי.3-פלג-אין",
    "effective_date": "תאריך תחולה",
    "list_price": "מחיר מחירון",
    "use_value": "שווי שימוש",
}


@dataclass
class ImportReport:
    read: int = 0
    imported: int = 0
    duplicates: int = 0
    rejected: Counter = field(default_factory=Counter)

    def __str__(self) -> str:
        lines = [f"נקראו {self.read:,} שורות · נקלטו {self.imported:,} · כפילויות על המפתח {self.duplicates}"]
        for reason, n in self.rejected.most_common():
            lines.append(f"  נדחו {n} — {reason}")
        return "\n".join(lines)


def _money(text: str) -> Decimal:
    return Decimal(text.strip())


def parse_rows(path: Path, report: ImportReport) -> list[dict]:
    rows: list[dict] = []
    seen: set[tuple] = set()
    with path.open(encoding="windows-1255", newline="") as fh:
        reader = csv.reader(fh)
        header = [h.strip() for h in next(reader)]
        try:
            idx = {key: header.index(col) for key, col in COLUMNS.items()}
        except ValueError as exc:
            raise SystemExit(f"עמודה חסרה במאגר: {exc}")
        for raw in reader:
            report.read += 1
            v = {key: raw[i].strip() for key, i in idx.items()}
            drive = DRIVE_CODES.get(v["drive"])
            if drive is None:
                report.rejected[f"קוד הנעה לא מוכר '{v['drive']}'"] += 1
                continue
            try:
                use_value = _money(v["use_value"])
                list_price = _money(v["list_price"]) if v["list_price"] else None
            except InvalidOperation:
                report.rejected["סכום לא תקין"] += 1
                continue
            key = (int(v["tax_year"]), int(v["reg_year"]), v["vehicle_type"],
                   v["manufacturer_code"].zfill(5), v["model_code"].zfill(5))
            if key in seen:
                report.duplicates += 1
                continue
            seen.add(key)
            rows.append(dict(
                tax_year=key[0], reg_year=key[1], vehicle_type=key[2],
                manufacturer_code=key[3], model_code=key[4],
                manufacturer_name=v["manufacturer_name"], model_description=v["model_description"],
                commercial_name=v["commercial_name"], fuel_code=v["fuel_code"], drive=drive,
                automatic=v["automatic"] == "1", list_price=list_price, use_value=use_value,
                effective_date=v["effective_date"],
            ))
    return rows


def import_csv(path: Path, session: Session) -> ImportReport:
    report = ImportReport()
    rows = parse_rows(path, report)
    tax_years = {r["tax_year"] for r in rows}
    with session.begin():  # הכול או כלום
        session.execute(delete(TaxVehicle).where(TaxVehicle.tax_year.in_(tax_years)))
        for i in range(0, len(rows), 5000):
            session.execute(insert(TaxVehicle), rows[i:i + 5000])
    report.imported = len(rows)
    return report


def main(argv: list[str]) -> None:
    path = Path(argv[1]) if len(argv) > 1 else get_settings().tax_csv_path
    engine = get_engine()
    Base.metadata.create_all(engine, tables=[TaxVehicle.__table__])
    report = import_csv(path, session_factory(engine)())
    print(report)


if __name__ == "__main__":
    main(sys.argv)
