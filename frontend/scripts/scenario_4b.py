"""Stage 4b scenario through the API (what the two browser windows do).

Noa Levi: Corolla Cross today, ATTO 3 as the alternative -> +661 with the pending-copay tag.
The rep confirms 350 -> the employee's updates show +611 and a copay of 350.

Usage (backend running with REP_ACCESS_TOKEN set):
    python frontend/scripts/scenario_4b.py [--base http://localhost:8000] [--token <REP_ACCESS_TOKEN>]
The token defaults to the REP_ACCESS_TOKEN environment variable.
"""
import argparse
import http.cookiejar
import json
import os
import sys
import urllib.error
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--token", default=os.environ.get("REP_ACCESS_TOKEN", ""))
args = ap.parse_args()
if not args.token:
    sys.exit("REP_ACCESS_TOKEN is required (env or --token)")

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, headers=None):
    req = urllib.request.Request(args.base + path, method=method, headers={"Content-Type": "application/json", **(headers or {})},
                                 data=None if body is None else json.dumps(body).encode())
    try:
        with opener.open(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"null")


def check(ok, what):
    print(("PASS " if ok else "FAIL ") + what)
    if not ok:
        sys.exit(1)


rep = {"X-Rep-Token": args.token}
status, turn = call("POST", "/api/conversations")
check(status == 201, "create conversation")
cid = turn["conversation_id"]
msg = f"/api/conversations/{cid}/messages"

status, turn = call("POST", msg, {"text": "אני מנהלת מוצר, שכר בסיס 18,500"})
check(status == 200, "Q1 accepted")
status, turn = call("POST", msg, {"fields": {"commissions": 0, "bonus": 0, "pension_percent": 6, "study_fund_percent": 2.5,
                                              "credit_points": 2.75, "children_under_23": 2}})
check(status == 200 and turn["reply"].startswith("רשמתי:"), "Q2 form saved in one structured request")
status, turn = call("POST", msg, {"text": "יש לי קורולה קרוס, השתתפות 300"})
check(status == 200, "current car accepted")
options = next((f for f in turn["expects"]["fields"] if f["field"] == "options"), None)
check(options is not None, "expects offers the alternatives")
atto = next(c["id"] for c in options["choices"] if c["label"].startswith("ATTO 3"))
status, turn = call("POST", msg, {"choices": [atto], "field": "options"})
[res] = turn["results"]
check(res["delta_display"] == "661" and res["copay_pending"], "ATTO 3 = +661 with the pending-copay tag")

status, questions = call("GET", "/api/rep/questions?status=OPEN", headers=rep)
check(status == 200 and len(questions) >= 1, "rep sees the open question")
# the rep never sees a conversation id; the newest question is the one this run just opened
q = max((x for x in questions if x["missing_field"] == "NEW_CAR_COPAY"), key=lambda x: x["created_at"])
check("salary" not in json.dumps(q) and "18500" not in json.dumps(q), "rep question carries no salary data")
status, _ = call("GET", "/api/rep/questions?status=OPEN", headers={"X-Rep-Token": "wrong"})
check(status == 401, "wrong token -> 401")
status, _ = call("POST", f"/api/rep/questions/{q['id']}/answer", {"value": "-1"}, headers=rep)
check(status == 422, "negative amount -> 422")

status, _ = call("POST", f"/api/rep/questions/{q['id']}/answer", {"value": "350"}, headers=rep)
check(status == 200, "rep answers 350 (the suggestion was shown as the primary button)")
status, _ = call("POST", f"/api/rep/questions/{q['id']}/answer", {"value": "350"}, headers=rep)
check(status == 409, "answering again -> 409")

status, upd = call("GET", f"/api/conversations/{cid}/updates?after=0")
[r] = upd["results"]
alt = r["result"]
check(alt["delta_display"] == "611" and not alt["copay_pending"], "employee updates show +611, tag gone")
check(alt["alternative"]["copay"].split(".")[0] == "350", "comparison table value: copay 350")
check(any(m["source"] == "rep" for m in upd["messages"]), "rep answer appears as a message")
print("scenario OK")
