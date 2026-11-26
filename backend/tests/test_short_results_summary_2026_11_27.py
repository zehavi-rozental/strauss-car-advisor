"""Finding #9 (27/11): the chat text above the result cards repeated every figure the cards show (each car's net, its
difference, each copay). The approved rule: the cards stay as they are; the text is a short summary of at most three
lines:
  1) where relevant, the direction for all the cars ("בכל 3 הרכבים הנטו שלכם יקטן."), then the car with the highest
     estimated net, that net and its difference from today;
  2) the main reason for that car, then "הפירוט המלא בכרטיסים." (the map: "הפירוט המלא במפת האפשרויות.");
  3) the existing next step, as its own paragraph.
No per-car net list and no copay list. Every figure comes from the engine result the cards show.
"""
from __future__ import annotations

from decimal import Decimal

from app.modules.conversation import templates as T
from app.modules.conversation.option_map import NET_ONLY
from app.modules.conversation.stage import ConversationState, OptionResult
from app.modules.conversation.templates import money
from app.shared.money import to_shekel

from .conftest import make_client

NOA = "שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים, קבוצה ב"
CARDS = "הפירוט המלא בכרטיסים."


def _item(label, delta, net, why="שווי השימוש גבוה יותר, ולכן המס יגדל."):
    result = {"status": "OK", "reason": "", "delta_display": str(delta), "copay_pending": False, "drivers": [],
              "why": why, "alternative": {"net_display": str(net), "copay": "250"}}
    return OptionResult(option_id=label, label=label, result=result)


def _text(*items, **state):
    return T.results_text(ConversationState(results=list(items), **state))


# ---------------------------------------------------------------- the template itself

def test_all_down_is_the_approved_example():
    t = _text(_item("NIRO (2025)", -1400, 12209), _item("MG4 (2025)", -1163, 12446),
              _item("KONA EV (2026)", -1500, 12109))
    assert t.splitlines() == [
        "בכל 3 הרכבים הנטו שלכם יקטן. MG4 (2025) משאיר את הנטו הגבוה ביותר: 12,446 ₪ בחודש, 1,163 ₪ פחות מהיום.",
        "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]


def test_all_up_two_cars_and_mixed():
    up = _text(_item("A", 100, 11100), _item("B", 300, 11300))
    assert up.splitlines()[0] == "בשני הרכבים הנטו שלכם יגדל. B משאיר את הנטו הגבוה ביותר: 11,300 ₪ בחודש, 300 ₪ יותר מהיום."
    mixed = _text(_item("A", -50, 10950), _item("B", 70, 11070), _item("C", 0, 11000))
    assert mixed.splitlines()[0].startswith("בחלק מהרכבים הנטו שלכם יגדל ובחלק יקטן. B משאיר את הנטו הגבוה ביותר")


def test_one_car_and_no_change():
    one = _text(_item("ATTO 3 (2025)", 661, 11693))
    assert one.splitlines() == ["בדקתי את ATTO 3 (2025). הנטו שלכם יגדל ב-661 ₪ בחודש, ל-11,693 ₪.",
                                "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]
    flat = _text(_item("A", 0, 11000, why=""), _item("B", 0, 11000, why=""))
    assert flat.splitlines() == ["בשני הרכבים הנטו שלכם לא ישתנה.", CARDS]


def test_a_tie_names_every_car_with_the_highest_net():
    t = _text(_item("A", 200, 11200), _item("B", 200, 11200), _item("C", 100, 11100))
    assert "A ו-B משאירים את הנטו הגבוה ביותר: 11,200 ₪ בחודש" in t.splitlines()[0]


def test_no_per_car_list_and_no_copay_list():
    t = _text(_item("NIRO (2025)", 539, 11571), _item("MG4 (2025)", 1013, 12045))
    assert "11,571" not in t and "539" not in t and "NIRO" not in t          # only the car with the highest net
    assert "הנטו החודשי המשוער:" not in t and "השתתפות" not in t and "250" not in t
    assert len(t.splitlines()) <= 2


def test_rank_says_how_many_were_checked_then_the_summary():
    t = _text(_item("A", 100, 11100), _item("B", 300, 11300), _item("C", 200, 11200), ranked_count=13)
    assert t.splitlines() == [
        "בדקתי את כל 13 הרכבים שאתם זכאים להם. B משאיר את הנטו הגבוה ביותר: 11,300 ₪ בחודש, 300 ₪ יותר מהיום.",
        "הסיבה העיקרית: שווי השימוש גבוה יותר, ולכן המס יגדל. " + CARDS]


# ---------------------------------------------------------------- through the API

def _start(engine):
    client = make_client(engine)
    cid = client.post("/api/conversations").json()["conversation_id"]

    def say(text):
        r = client.post(f"/api/conversations/{cid}/messages", json={"text": text})
        assert r.status_code == 200, r.text
        return r.json()["reply"]

    for text in (NOA, "יש לי קורולה קרוס", "300"):
        say(text)
    return say, lambda: client.get(f"/api/conversations/{cid}").json()


def _best(view):
    ok = [r for r in view["results"] if r["result"]["status"] == "OK"]
    return max(ok, key=lambda r: Decimal(r["result"]["delta_display"])), ok


def _amount(value) -> str:
    return money(to_shekel(Decimal(str(value))))


def _assert_summary(reply, view, follow_up):
    paragraphs = reply.split("\n\n")
    summary = paragraphs[0].splitlines()
    best, ok = _best(view)
    delta = to_shekel(Decimal(best["result"]["delta_display"]))
    assert len(summary) == 2, reply
    assert f"{best['label']} משאיר את הנטו הגבוה ביותר: {_amount(best['result']['alternative']['net_display'])} בחודש, "\
           f"{money(abs(delta))} {'יותר' if delta > 0 else 'פחות'} מהיום." in summary[0]
    assert summary[1] == f"הסיבה העיקרית: {best['result']['why']} {CARDS}"
    for other in ok:
        if other is not best:                                   # no per-car list: the other cards' figures stay there
            assert _amount(other["result"]["alternative"]["net_display"]) not in reply
    assert "הנטו החודשי המשוער:" not in reply and "לפי הקטלוג" not in reply and "חישבתי עם ההשתתפות" not in reply
    assert paragraphs[-1] == follow_up and len(paragraphs) == 2      # the next step, its own paragraph


def test_a_chosen_comparison_is_a_short_summary_and_the_next_step(engine):
    say, view = _start(engine)
    reply = say("תשווה בין MG4 ל-NIRO")
    assert len(view()["results"]) == 2
    _assert_summary(reply, view(), T.NEXT_AFTER_RESULTS)
    assert reply.startswith("בשני הרכבים הנטו שלכם")


def test_best_value_is_a_short_summary_and_the_next_step(engine):
    say, view = _start(engine)
    reply = say("מה הכי משתלם לי?")
    assert reply.startswith("בדקתי את כל ")
    _assert_summary(reply, view(), T.NEXT_AFTER_RANK)


def test_the_map_names_only_the_car_with_the_highest_net(engine):
    say, view = _start(engine)
    reply = say("השווה את כל האפשרויות שלי")
    entries = [e for e in view()["option_map"]["entries"] if e["status"] in ("full", "catalog_copay")]
    leader = next(e for e in entries if e["option_id"] in view()["option_map"]["leaders"])
    net = _amount(leader["result"]["alternative"]["net_display"])
    assert f"{leader['label']} משאיר את הנטו הגבוה ביותר: {net} בחודש" in reply
    assert "הפירוט המלא במפת האפשרויות." in reply and NET_ONLY in reply
    others = {_amount(e["result"]["alternative"]["net_display"]) for e in entries} - {net}
    assert not any(n in reply for n in others)
