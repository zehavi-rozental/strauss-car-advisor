"""Readiness at deployment (20/11): the app is up AND its database answers.

    python -m app.readiness          (inside the app container; vm-setup-and-test.sh runs it after liveness)

The container's health check is liveness only (/api/live: the process answers, no database), so a Neon outage
never restarts a working app. A deployment, though, must not pass while the database is unreachable: this asks the
running app's own /api/health and succeeds only when it says db is true AND status is "ok".

- The address is the app inside this container: 127.0.0.1 and the port render_start.py gives uvicorn ($PORT, 10000
  by default). No proxy from the environment is used and no redirect is followed.
- Transient (retried until the deadline): no answer yet (refused, reset, timeout), HTTP 502/503/504, or a valid
  answer saying the database is not reachable yet (db false, status "degraded": Neon waking up).
- Permanent (fails at once, no waiting): any other HTTP status, a body that is not the health JSON object, a missing
  or non-boolean "db", an unknown "status", or the two disagreeing.
- Output: PASS / FAIL lines with fixed wording; from the answer only the HTTP status code, db as true/false and a
  known status word. Never the body, a header, an exception message or any other value.

Exit codes: 0 ready; 1 not ready by the deadline; 2 a permanent bad answer; 3 the check itself failed.
"""
from __future__ import annotations

import http.client
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Callable, Mapping, NamedTuple, Optional

HOST = "127.0.0.1"
DEFAULT_PORT = "10000"           # render_start.py: os.environ.get("PORT", "10000")
PATH = "/api/health"
MAX_BODY = 64 * 1024
DEADLINE_S = 300.0               # about 5 minutes for a cold start or a short outage
PAUSE_S = 5.0
TIMEOUT_S = 10.0
TRANSIENT_HTTP = frozenset({502, 503, 504})

READY, NOT_READY, BAD_ANSWER, CHECK_ERROR = 0, 1, 2, 3


class Result(NamedTuple):
    kind: str                    # "ready" | "transient" | "permanent"
    reason: str                  # fixed wording only (see the module docstring)


def health_url(env: Mapping[str, str] = os.environ) -> str:
    port = env.get("PORT", DEFAULT_PORT)
    if not (port.isdigit() and 0 < int(port) < 65536):
        raise ValueError("PORT is not a port number")
    return f"http://{HOST}:{int(port)}{PATH}"


def classify(status_code: int, raw: bytes) -> Result:
    """What one answer of /api/health means. The ONLY "ready": HTTP 200, a JSON object, db true and status "ok"."""
    if status_code in TRANSIENT_HTTP:
        return Result("transient", f"HTTP {status_code}")
    if status_code != 200:
        return Result("permanent", f"HTTP {status_code}")
    if len(raw) > MAX_BODY:
        return Result("permanent", "answer too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return Result("permanent", "answer is not JSON")
    if not isinstance(data, dict):
        return Result("permanent", "answer is not a JSON object")
    db, status = data.get("db"), data.get("status")
    if not isinstance(db, bool):
        return Result("permanent", "db missing or not true/false")
    if status not in ("ok", "degraded"):
        return Result("permanent", "status missing or unknown")
    if db is True and status == "ok":
        return Result("ready", "db true, status ok")
    if db is False and status == "degraded":
        return Result("transient", "database not reachable yet (db false, status degraded)")
    return Result("permanent", f"db {'true' if db else 'false'} disagrees with status {status}")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None              # a 3xx is then an HTTPError: never followed


def _opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def probe(url: str, timeout: float) -> Result:
    try:
        with _opener().open(url, timeout=timeout) as response:
            code, raw = response.status, response.read(MAX_BODY + 1)
    except urllib.error.HTTPError as e:                # 3xx (not followed), 4xx, 5xx
        e.close()
        return classify(e.code, b"")
    except (urllib.error.URLError, OSError, http.client.HTTPException) as e:   # refused, reset, timeout, cut off
        return Result("transient", f"no answer ({type(e).__name__})")
    return classify(code, raw)


def main(*, url: Optional[str] = None, deadline_s: float = DEADLINE_S, pause_s: float = PAUSE_S,
         timeout_s: float = TIMEOUT_S, probe: Callable[[str, float], Result] = probe,
         clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep,
         out: Callable[[str], None] = print) -> int:
    try:
        target = url or health_url()
        end = clock() + deadline_s
        while True:
            result = probe(target, timeout_s)
            if result.kind == "ready":
                out("PASS  database ready (the app reports db true, status ok)")
                return READY
            if result.kind != "transient":
                out(f"FAIL  readiness: {result.reason} (not retried)")
                return BAD_ANSWER
            if clock() + pause_s >= end:
                out(f"FAIL  database not ready after {deadline_s:.0f} s: {result.reason}")
                return NOT_READY
            sleep(pause_s)
    except Exception as e:                             # the check itself broke: a failure, never a success
        out(f"FAIL  readiness check error ({type(e).__name__})")
        return CHECK_ERROR


if __name__ == "__main__":
    sys.exit(main())
