"""STRAUSS DRIVE end-to-end check at the API level, against any deployment (a local server, the Oracle VM, or the
public Cloudflare link). Synthetic data only; never prints a secret. Exit code 0 only when every check passes.
    python e2e_api.py <base-url> [payslip.pdf]      env REP_TOKEN (optional): the rep screen checks
"""
import os
import statistics
import sys
import time

import httpx

BASE = sys.argv[1].rstrip("/")
PAYSLIP = sys.argv[2] if len(sys.argv) > 2 else ""
REP_TOKEN = os.environ.get("REP_TOKEN") or os.environ.get("REP_ACCESS_TOKEN", "")
fails, timings = [], {"code": [], "ai": [], "other": []}


def check(ok, label, detail=""):
    print(f"{'PASS' if ok else 'FAIL'}  {label}{('  ' + detail) if detail else ''}", flush=True)
    if not ok:
        fails.append(label)
    return ok


def timed(kind, fn):
    s = time.perf_counter(); r = fn(); ms = (time.perf_counter() - s) * 1000
    timings[kind].append(ms)
    return r, ms


class Chat:
    def __init__(self):
        self.c = httpx.Client(base_url=BASE, timeout=90)
        r, _ = timed("other", lambda: self.c.post("/api/conversations"))
        assert r.status_code == 201, r.status_code
        self.cid = r.json()["conversation_id"]

    def say(self, text):
        r, ms = timed("other", lambda: self.c.post(f"/api/conversations/{self.cid}/messages", json={"text": text}))
        engine = r.json().get("engine") if r.headers.get("content-type", "").startswith("application/json") else None
        timings["ai" if engine == "ai" else "code"].append(timings["other"].pop())
        return r

    def view(self):
        return self.c.get(f"/api/conversations/{self.cid}").json()


def deltas(view):
    return [(r["option_id"], round(float(r["result"]["delta_display"]))) for r in view.get("results", [])
            if r.get("result") and r["result"].get("delta_display") is not None]


NOA = "אני מנהלת מוצר, שכר בסיס 18,500, אין עמלות ואין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 2 ילדים"
CASES = [
    ("E-001 Noa: hybrid to electric", [NOA, "יש לי קורולה קרוס", "300", "אטו 3"], "CAR-10", 661),
    ("E-002 David: family track", ["אני נציג מכירות שטח, שכר בסיס 9,800, עמלות 2,400, אין בונוס, פנסיה 6%, אין קרן השתלמות, 2.25 נקודות זיכוי, 5 ילדים", "נירו", "250", "קרניבל"], "CAR-19", -3522),
    ("E-003 Rachel: no car with maintenance", ["אני מפתחת תוכנה, שכר בסיס 21,000, אין עמלות, אין בונוס, פנסיה 6.5%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, אין ילדים", "אין לי רכב", "1,500", "MG4"], "CAR-06", -1938),
    ("E-004 Yossi: commissions", ["אני מנהל אזור מכירות, שכר בסיס 16,000, עמלות 3,000, אין בונוס, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 3 ילדים", "טוסון", "400", "EV3"], "CAR-11", 1207),
    ("E-005 Michal: opting out", ["אני מנהלת פיתוח, שכר בסיס 27,000, אין עמלות, אין בונוס, פנסיה 7%, קרן השתלמות 2.5%, 2.75 נקודות זיכוי, 1 ילדים", "מאזדה 3", "350", "ויתור"], "OPT_OUT", 3562),
    ("E-006 Eli: bonus, high salary", ["אני מנהל מחלקה, שכר בסיס 32,000, אין עמלות, בונוס 2,000, פנסיה 6%, קרן השתלמות 2.5%, 2.25 נקודות זיכוי, 4 ילדים", "אוקטביה", "500", "מודל 3"], "CAR-14", 721),
]

print(f"== {BASE}")
h = httpx.get(f"{BASE}/api/health", timeout=60).json()
check(h.get("status") == "ok" and h.get("db") is True, "health: server and database", f"engine={h.get('engine')} payslip={h.get('payslip')}")
check(not any(k in str(h).lower() for k in ("password", "secret", "api_key", "postgres://", "postgresql://")), "health leaks no secret")
page = httpx.get(f"{BASE}/employee", timeout=30)
check(page.status_code == 200 and "<div id=\"root\">" in page.text, "the React page is served (/employee)")

print("== the six reference employees (engine = Strauss reference)")
for name, msgs, option, want in CASES:
    chat = Chat()
    for m in msgs:
        chat.say(m)
    got = deltas(chat.view())
    check(got == [(option, want)], name, f"got {got}")

print("== a conversation with context, a correction and a ranking")
chat = Chat()
for m in [NOA, "יש לי קורולה קרוס", "300", "אטו 3"]:
    chat.say(m)
before = deltas(chat.view())
r = chat.say("טעיתי, השכר שלי 20,000")
v = chat.view()
after = deltas(v)
check(r.status_code == 200 and after and after != before and not v.get("results_stale"), "a salary correction recalculates", f"{before} -> {after}")
r = chat.say("מה הכי משתלם לי?")
ranked = deltas(chat.view())
check(len(ranked) == 3 and [d for _, d in ranked] == sorted([d for _, d in ranked], reverse=True), "\"מה הכי משתלם\" ranks the top three", str(ranked))
r = chat.say("למה דווקא הרכב הראשון ברשימה?")
check(r.status_code == 200 and len(r.json().get("reply", "")) > 20, "a free follow-up question is answered in context", f"engine={r.json().get('engine')}")
again = httpx.Client(base_url=BASE, cookies=chat.c.cookies, timeout=30).get(f"/api/conversations/{chat.cid}")
check(again.status_code == 200 and len(again.json()["messages"]) >= 12, "an existing conversation reopens (same cookie)", f"{len(again.json().get('messages', []))} messages")
check(httpx.get(f"{BASE}/api/conversations/{chat.cid}", timeout=30).status_code == 404, "another browser cannot open it (no cookie -> 404)")

print("== free text with Gemini")
chat = Chat()
r = chat.say("היי, יש לי היום רכב חברה אבל אני לא זוכרת בדיוק איזה דגם, אולי טויוטה")
check(r.status_code == 200 and r.json().get("reply"), "free text about today's car", f"engine={r.json().get('engine')}")
r = chat.say("מה ההבדל בין רכב חשמלי להיברידי מבחינת המס?")
check(r.status_code == 200 and r.json().get("reply"), "a domain question", f"engine={r.json().get('engine')}")

print("== catalog")
cat = httpx.get(f"{BASE}/api/catalog", timeout=30)
items = cat.json() if cat.status_code == 200 else []
check(isinstance(items, list) and len(items) >= 10 and all({"code", "label", "group", "use_value"} <= set(i) for i in items),
      "catalog: cars with group and use value", f"{len(items)} cars")
s = httpx.get(f"{BASE}/api/catalog/search", params={"q": "קורולה"}, timeout=30)
check(s.status_code == 200 and len(s.json()) >= 1, "catalog search (קורולה)", f"{len(s.json())} matches")

print("== payslip")
if PAYSLIP and os.path.exists(PAYSLIP) and h.get("payslip"):
    with open(PAYSLIP, "rb") as f:
        r, ms = timed("ai", lambda: httpx.post(f"{BASE}/api/payslip/extract", files={"file": ("payslip.pdf", f, "application/pdf")}, timeout=120))
    j = r.json() if r.status_code == 200 else {}
    found = {x["field"]: x["value"] for x in j.get("fields", []) if x.get("status") == "found"}
    check(r.status_code == 200 and j.get("status") == "found" and len(found) == 7 and "." in str(found.get("credit_points", "")),
          "payslip read by Gemini Vision (synthetic): all 7 fields, credit points keep their fraction",
          f"status={j.get('status')} found={len(found)} {ms:.0f} ms")
else:
    check(False, "payslip extraction", "skipped: no file or payslip disabled")
big = httpx.post(f"{BASE}/api/payslip/extract", files={"file": ("big.pdf", b"0" * (9 * 1024 * 1024), "application/pdf")}, timeout=120)
check(big.status_code == 413, "a payslip over 8 MB is refused (413, no model call)")

print("== errors and missing information")
chat = Chat()
r = chat.c.post(f"/api/conversations/{chat.cid}/messages", json={"fields": {"base_salary": "-5", "pension_percent": "40"}})
check(r.status_code == 422 and len(r.json().get("errors", {})) == 2, "invalid form values: 422, every bad field named")
r = chat.c.post(f"/api/conversations/{chat.cid}/messages", json={})
check(r.status_code == 422, "an empty message body: 422")
r = chat.say("אני בקבוצה ג")
check(r.status_code == 200 and not r.json().get("results"), "missing data: no calculation, a question instead")

print("== editing data through the form recalculates")
chat = Chat()
for m in [NOA, "יש לי קורולה קרוס", "300", "אטו 3"]:
    chat.say(m)
before = deltas(chat.view())
r = chat.c.post(f"/api/conversations/{chat.cid}/messages", json={"fields": {"base_salary": "21000"}})
after = deltas(chat.view())
check(r.status_code == 200 and "עדכנתי" in r.json().get("reply", "") and after and after != before,
      "the profile form (base salary 18,500 -> 21,000) is saved and recalculated", f"{before} -> {after}")

print("== representative: question, answer, back to the employee")
if REP_TOKEN:
    rep = httpx.Client(base_url=BASE, timeout=60, headers={"X-Rep-Token": REP_TOKEN, "X-Rep-Session": "e2e-synthetic-session-0001"})
    chat = Chat()
    chat.say(NOA)
    q = chat.c.post(f"/api/conversations/{chat.cid}/rep-question",
                    json={"question": "בדיקה אוטומטית: האם בקורולה יש מצלמת רוורס?", "intent": "general"})
    qid = q.json().get("question_id") if q.status_code == 200 else None
    check(bool(qid), "an employee sends a question to the representative", f"{q.status_code} {q.json().get('status') if q.status_code == 200 else ''}")
    open_q = rep.get("/api/rep/questions", params={"status": "OPEN"})
    mine = [x for x in open_q.json() if x.get("id") == qid] if open_q.status_code == 200 else []
    check(bool(mine) and "salary" not in str(mine[0]).lower(), "the rep screen lists it, without salary data", f"{open_q.status_code}, {len(open_q.json()) if open_q.status_code == 200 else 0} open")
    a = rep.post(f"/api/rep/questions/{qid}/answer", json={"value": "כן, יש מצלמת רוורס (תשובת בדיקה)"})
    check(a.status_code == 200, "the representative answers", str(a.status_code))
    seen = False
    for _ in range(10):
        msgs = chat.c.get(f"/api/conversations/{chat.cid}/updates", params={"after": 0}).json().get("messages", [])
        if any("מצלמת רוורס (תשובת בדיקה)" in m.get("content", "") for m in msgs):
            seen = True; break
        time.sleep(1)
    check(seen, "the answer reaches the employee's conversation")
    check(rep.post(f"/api/rep/questions/{qid}/answer", json={"value": "שוב"}).status_code == 409, "answering twice is refused (409)")
    check(httpx.get(f"{BASE}/api/rep/questions", headers={"X-Rep-Token": "a-guess"}, timeout=30).status_code == 401, "a wrong rep code: 401")

print("== stability: 20 sequential health checks")
lat = []
for _ in range(20):
    s0 = time.perf_counter(); ok = httpx.get(f"{BASE}/api/health", timeout=30).status_code == 200; lat.append((time.perf_counter() - s0) * 1000)
    if not ok: break
check(len(lat) == 20, "20/20 health checks answered", f"median {statistics.median(lat):.0f} ms, max {max(lat):.0f} ms")

print("== security")
c = httpx.post(f"{BASE}/api/conversations", headers={"Origin": "https://evil.example"}, timeout=30)
cookie = c.headers.get("set-cookie", "")
check("httponly" in cookie.lower() and "samesite=lax" in cookie.lower(), "session cookie HttpOnly + SameSite=Lax")
if BASE.startswith("https://"):
    check("secure" in cookie.lower(), "session cookie Secure over HTTPS")
check("access-control-allow-origin" not in {k.lower() for k in c.headers}, "no CORS for another origin")
check(httpx.get(f"{BASE}/api/rep/questions", timeout=30).status_code == 401, "rep screen without a code: 401")
if REP_TOKEN:
    check(httpx.get(f"{BASE}/api/rep/questions", headers={"X-Rep-Token": REP_TOKEN}, timeout=30).status_code == 200, "rep screen with its code: 200")

print("== response times")
for kind, label in (("code", "answers by code"), ("ai", "answers with Gemini")):
    xs = timings[kind]
    if xs:
        print(f"INFO  {label}: {len(xs)} requests, median {statistics.median(xs):.0f} ms, max {max(xs):.0f} ms")
print(f"== {'ALL PASS' if not fails else str(len(fails)) + ' FAILED: ' + '; '.join(fails)}")
sys.exit(1 if fails else 0)
