"""Car items on a payslip are never pay (QA 22/10, ten synthetic payslips through live Gemini).

Found: Gemini reported the employee's car copay as `other_addition` on 7 of 10 payslips, and the review then
offered "לשייך לבונוס" for it when the bonus row was empty. Now a car copay and car maintenance are their own
information keys (`car_copay`, `car_maintenance`), recognised on the printed label in code whatever key the model
chose, and never assignable. Also: a value printed for a profile field but impossible for it (credit points
"52.3", printed reversed) is "ambiguous" and never filled, and a doubtful information value (a use value of 3.4)
is flagged, not shown as a fact.

The recorded answers are in tests/fixtures/payslip_live_2026_10_22 (JSON only, no PDF, no personal data).
"""
from __future__ import annotations

import json
from decimal import Decimal

import pytest

from app.modules.payslip.extract import ITEM_KEYS, _SYSTEM_PROMPT, extract
from app.modules.payslip.models import SUSPECT_NOTE

from .conftest import BACKEND

LIVE = BACKEND / "tests" / "fixtures" / "payslip_live_2026_10_22"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32


class FakeVision:
    def __init__(self, response: str):
        self.response = response

    def extract(self, data, mime, prompt, timeout_s):
        return self.response


def run(snapshot, response: str):
    return extract(JPEG, "image/jpeg", FakeVision(response), snapshot, timeout_s=10)


def answer(*items) -> str:
    return json.dumps({"is_payslip": True, "readable": True, "items": list(items)}, ensure_ascii=False)


def item(key, value, unit="ILS", label="", certainty="high"):
    return {"key": key, "value": value, "unit": unit, "certainty": certainty, "label": label}


def recorded(snapshot, name: str):
    return run(snapshot, (LIVE / f"{name}.json").read_text(encoding="utf-8"))


def fields(result) -> dict:
    return {f.field: f for f in result.fields}


def infos(result) -> dict:
    return {i["key"]: i for i in result.as_dict()["info"]}


# ---------- the copay is never pay ----------

@pytest.mark.parametrize("key", ["other_addition", "bonus", "commissions", "base_salary"])
@pytest.mark.parametrize("label", ["השתתפות עובד ברכב", "השתתפות עובד/ת ברכב", "השתתפות ברכב", "Employee car copay"])
def test_a_car_copay_is_information_whatever_key_the_model_chose(snapshot, key, label):
    # a payslip with no commissions or bonus row: the rows the copay could have been assigned to are empty
    result = run(snapshot, answer(item("base_salary", 15000, label="שכר בסיס"), item(key, 300, label=label)))
    f = fields(result)
    assert f["base_salary"].value == Decimal(15000)
    assert f["commissions"].status == "missing" and f["bonus"].status == "missing"
    copay = infos(result)["car_copay"]
    assert (copay["value"], copay["assignable"]) == ("300", False)
    assert "other_addition" not in infos(result)


@pytest.mark.parametrize("label", ["אחזקת רכב", "החזר אחזקת רכב", "Car maintenance"])
def test_car_maintenance_is_its_own_information_and_never_pay(snapshot, label):
    result = run(snapshot, answer(item("other_addition", 1500, label=label), item("bonus", 1500, label=label)))
    f = fields(result)
    assert f["bonus"].status == "missing" and f["commissions"].status == "missing"
    maintenance = infos(result)["car_maintenance"]
    assert (maintenance["value"], maintenance["assignable"]) == ("1500", False)
    assert "השתתפות" not in maintenance["label"]          # maintenance and copay are different items


@pytest.mark.parametrize("label", ["תוספת חודשית", "שעות נוספות", "תוספת משמרות", "תוספת קבועה", ""])
def test_a_real_addition_to_pay_can_still_be_assigned(snapshot, label):
    addition = infos(run(snapshot, answer(item("other_addition", 1100, label=label))))["other_addition"]
    assert (addition["value"], addition["assignable"], addition["suspect"]) == ("1100", True, False)


def test_the_prompt_and_schema_name_the_car_items():
    assert {"car_copay", "car_maintenance"} <= set(ITEM_KEYS)
    additions = next(line for line in _SYSTEM_PROMPT.splitlines() if line.strip().startswith("other_addition"))
    assert "אחזקת רכב" not in additions
    assert "never other_addition, commissions or bonus" in _SYSTEM_PROMPT


# ---------- the recorded live answers (22/10) ----------

# What each payslip says, per profile field (None: not on the payslip as the field needs it), its copay.
LIVE_EXPECTED = {
    "ref_e002": ({"base_salary": "9800", "commissions": "2400", "bonus": "0", "credit_points": "2.25",
                  "children_under_23": "5"}, "250"),
    "ref_e001": ({"base_salary": "18500", "commissions": "0", "bonus": "0", "credit_points": "2.75",
                  "children_under_23": "2"}, "300"),
    "ref_e004": ({"base_salary": "16000", "commissions": "3000", "bonus": "0", "credit_points": "2.25",
                  "children_under_23": "3"}, "400"),
    "ref_e003": ({"base_salary": "21000", "commissions": "0", "bonus": "0", "credit_points": "2.75",
                  "children_under_23": "0"}, None),
    "01_classic_table": ({"base_salary": "14200", "commissions": "1800", "bonus": "650", "credit_points": "2.25"},
                         None),
    "02_two_columns": ({"base_salary": "22000", "commissions": "0", "bonus": "1500", "children_under_23": "3"},
                       None),
    "03_compact_rows": ({"base_salary": "11400", "commissions": "0", "bonus": "0", "children_under_23": "0"}, None),
    "04_bilingual": ({"base_salary": "17300", "commissions": "2250", "bonus": "0", "credit_points": "2.75"}, "350"),
    "05_two_pages": ({"base_salary": "15800", "commissions": "3400", "bonus": "0", "credit_points": "2.75",
                      "children_under_23": "2"}, "300"),
    "06_scanned_image_only": ({"base_salary": "13600", "commissions": "1200", "bonus": "800",
                               "pension_percent": "6", "study_fund_percent": "2.5", "credit_points": "2.25",
                               "children_under_23": "1"}, "250"),
}


@pytest.mark.parametrize("name", sorted(LIVE_EXPECTED))
def test_each_recorded_payslip_fills_only_what_it_prints(snapshot, name):
    expected, copay = LIVE_EXPECTED[name]
    result = recorded(snapshot, name)
    found = {f.field: str(f.value) for f in result.fields if f.status == "found"}
    assert found == expected
    info = infos(result)
    if copay is None:
        assert "car_copay" not in info
    else:
        assert (info["car_copay"]["value"], info["car_copay"]["assignable"]) == (copay, False)
    # a copay never sits under an addition the employee could assign to pay
    assert all(not (i["key"] == "other_addition" and "רכב" in i["source_label"]) for i in result.as_dict()["info"])


@pytest.mark.parametrize("name", sorted(LIVE_EXPECTED))
def test_no_recorded_payslip_puts_gross_net_or_a_car_value_into_pay(snapshot, name):
    result = recorded(snapshot, name)
    pay = {f.value for f in result.fields if f.field in ("base_salary", "commissions", "bonus") and f.value}
    info = {i.key: i.value for i in result.info}
    for key in ("gross_salary", "net_pay", "car_use_value", "car_copay", "car_maintenance"):
        if key in info and info[key] != 0 and key != "gross_salary":
            assert info[key] not in pay, key
    if "gross_salary" in info and "base_salary" in fields(result):
        base = fields(result)["base_salary"].value
        assert base is None or base <= info["gross_salary"]


def test_the_maintenance_on_the_opt_out_payslip_is_not_pay(snapshot):
    result = recorded(snapshot, "ref_e003")
    assert Decimal(1500) not in {f.value for f in result.fields}


# ---------- printed, but impossible: ambiguous, never filled ----------

@pytest.mark.parametrize("name", ["02_two_columns", "03_compact_rows"])
def test_reversed_credit_points_are_ambiguous_not_missing(snapshot, name):
    points = fields(recorded(snapshot, name))["credit_points"]
    assert (points.status, points.value, points.confident) == ("ambiguous", None, False)
    assert "לא מילאנו" in points.note and "52" not in points.note


def test_an_impossible_value_is_never_guessed_into_a_fix(snapshot):
    points = fields(run(snapshot, answer(item("credit_points", 52.3, "count", label="נקודות זיכוי"))))["credit_points"]
    assert points.value is None and points.status == "ambiguous"     # not 3.25, not 5.23


# ---------- information values that do not look right ----------

def test_a_use_value_read_from_reversed_digits_is_flagged(snapshot):
    use = infos(recorded(snapshot, "02_two_columns"))["car_use_value"]
    assert (use["value"], use["suspect"], use["note"]) == ("3.4", True, SUSPECT_NOTE)


@pytest.mark.parametrize("value,suspect", [(0, False), (2950, False), (4070, False), (20000, False),
                                           (3.4, True), (40, True), (25000, True)])
def test_a_use_value_is_checked_against_the_conversations_own_limits(snapshot, value, suspect):
    use = infos(run(snapshot, answer(item("car_use_value", value, label="שווי שימוש ברכב"))))["car_use_value"]
    assert use["suspect"] is suspect


@pytest.mark.parametrize("value,suspect", [(0, False), (250, False), (5, True)])
def test_a_copay_is_checked_too_and_zero_is_fine(snapshot, value, suspect):
    copay = infos(run(snapshot, answer(item("other_addition", value, label="השתתפות ברכב"))))["car_copay"]
    assert copay["suspect"] is suspect and copay["assignable"] is False


def test_a_suspect_addition_is_never_assignable(snapshot):
    # an addition has no limits of its own; the rule holds for any flagged item
    from app.modules.payslip.models import InfoItem
    assert InfoItem("other_addition", Decimal(5), "amount", suspect=True).as_dict()["assignable"] is False


def test_the_recorded_answers_carry_no_personal_data():
    for path in LIVE.glob("*.json"):
        data = json.loads(path.read_text(encoding="utf-8"))
        assert set(data) == {"is_payslip", "readable", "items"}, path.name
        assert all(set(i) <= {"key", "label", "value", "unit", "certainty"} for i in data["items"]), path.name


# ---------- unclear labels: never assumed to be pay, never assumed to be a car (QA 22/10, second pass) ----------
# A copay Gemini files under bonus / commissions with a label that names no car ("השתתפות", or none at all)
# used to be found with confidence and preselected "לעדכן": one blanket "אישור ושמירה" saved it as pay.

@pytest.mark.parametrize("key", ["bonus", "commissions"])
@pytest.mark.parametrize("label", ["השתתפות", "השתתפות עובד", "", "  ", None])
def test_pay_read_from_an_unclear_label_is_shown_but_never_preselected(snapshot, key, label):
    raw = item(key, 300, label=label) if label is not None else {k: v for k, v in item(key, 300).items() if k != "label"}
    f = fields(run(snapshot, answer(item("base_salary", 15000, label="שכר בסיס"), raw)))[key]
    # still shown with its value (it may really be pay), but unconfident: the review does not tick "לעדכן"
    assert (f.status, f.value, f.confident) == ("found", Decimal(300), False)
    assert "ייתכן שזה ניכוי" in f.note and "רק אם זו באמת" in f.note
    assert "car_copay" not in infos(run(snapshot, answer(raw)))        # not assumed to be a car either


@pytest.mark.parametrize("key,label", [("bonus", "בונוס"), ("bonus", "מענק"), ("bonus", "Bonus / סונוב"),
                                       ("commissions", "עמלות"), ("commissions", "עמלת מכירות"),
                                       ("commissions", "Commission / תולמע")])
def test_pay_with_a_clear_label_is_still_found_with_confidence(snapshot, key, label):
    f = fields(run(snapshot, answer(item(key, 1800, label=label))))[key]
    assert (f.status, f.confident, f.note) == ("found", True, "")


@pytest.mark.parametrize("label", ["השתתפות", "השתתפות עובד", "", "השתתפות בהוצאות טלפון"])
def test_an_addition_with_an_unclear_label_is_shown_for_a_decision_not_discarded(snapshot, label):
    addition = infos(run(snapshot, answer(item("other_addition", 200, label=label))))["other_addition"]
    # not a car, not thrown away: marked unclear, explained, and the employee may still assign it on purpose
    assert (addition["value"], addition["unclear"], addition["assignable"]) == ("200", True, True)
    assert "לא ברור מהתלוש אם זו תוספת לשכר או ניכוי" in addition["note"]


@pytest.mark.parametrize("label", ["תוספת חודשית", "שעות נוספות", "תוספת משמרות", "החזר הוצאות"])
def test_a_clear_addition_is_not_marked_unclear(snapshot, label):
    addition = infos(run(snapshot, answer(item("other_addition", 1000, label=label))))["other_addition"]
    assert (addition["unclear"], addition["assignable"]) == (False, True)


@pytest.mark.parametrize("key", ["bonus", "commissions", "other_addition"])
def test_a_participation_that_names_the_car_is_a_car_copay(snapshot, key):
    info = infos(run(snapshot, answer(item(key, 300, label="השתתפות עובד ברכב"))))
    assert (info["car_copay"]["assignable"], info["car_copay"]["unclear"]) == (False, False)


def test_the_recorded_payslips_keep_their_confident_pay_fields(snapshot):
    # every commissions / bonus row in the ten live answers had a clear label: none is downgraded
    for name in LIVE_EXPECTED:
        for f in recorded(snapshot, name).fields:
            if f.field in ("commissions", "bonus") and f.status == "found":
                assert f.confident, (name, f.field)
