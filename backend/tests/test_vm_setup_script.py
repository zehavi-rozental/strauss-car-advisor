"""deploy/oracle/vm-setup-and-test.sh (20/11): a deployment passes only when the app is live AND its database answers.

The container's health check is liveness only (/api/live), so "healthy" no longer says anything about the database;
the script checks the database explicitly with app.readiness, after liveness and before HTTPS and the end-to-end run.
The readiness block itself is run here in bash with a stand-in `docker`, so the pass and fail paths are executed.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "oracle" / "vm-setup-and-test.sh"
READINESS = "docker compose exec -T app python -m app.readiness"
BASH = shutil.which("bash")


def _lines() -> list[str]:
    return SCRIPT.read_text(encoding="utf-8").splitlines()


def _index(fragment: str) -> int:
    hits = [i for i, line in enumerate(_lines()) if fragment in line]
    assert len(hits) == 1, f"{fragment!r} found {len(hits)} times"
    return hits[0]


def _readiness_block() -> str:
    """The readiness command and its continuation lines, exactly as the script has them."""
    lines, start = _lines(), _index(READINESS)
    block = [lines[start]]
    while block[-1].rstrip().endswith("\\"):
        block.append(lines[start + len(block)])
    return "\n".join(block)


def test_s1_order_liveness_then_readiness_then_https_then_end_to_end():
    liveness = _index('grep -q healthy && ok "app live')
    readiness = _index(READINESS)
    https = _index('echo "== HTTPS')
    e2e = _index("python /tmp/e2e_api.py")
    assert liveness < readiness < https < e2e


def test_s1_a_readiness_failure_stops_the_deployment():
    block = _readiness_block()
    assert block.splitlines()[-1].strip() == (
        '|| { bad "database not available: the app answers but the database does not"; exit 1; }')
    assert '&& ok "database reachable (the app reports db true)"' in block


def test_s3_no_pipe_on_the_readiness_command_and_liveness_claims_no_database():
    block = _readiness_block().replace("||", "").replace("&&", "")
    assert "|" not in block                                          # a pipe could hide the exit code
    live_line = _lines()[_index('grep -q healthy && ok "app live')]
    assert "no database" in live_line and "database reachable" not in live_line


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_s2_the_script_parses():
    assert subprocess.run([BASH, "-n", str(SCRIPT)], capture_output=True).returncode == 0


def _run_block(readiness_exit: int) -> subprocess.CompletedProcess:
    """The script's own readiness block, with ok/bad as in the script and `docker` standing in for the container."""
    program = "\n".join([
        "set -uo pipefail",
        "ok() { printf 'PASS  %s\\n' \"$*\"; }; bad() { printf 'FAIL  %s\\n' \"$*\"; }",
        "docker() {",
        f"  [ \"$*\" = \"{READINESS[len('docker '):]}\" ] || {{ echo \"unexpected docker call: $*\"; return 99; }}",
        f"  return {readiness_exit}",
        "}",
        _readiness_block(),
        'echo "REACHED THE NEXT STEP"',
    ])
    return subprocess.run([BASH, "-c", program], capture_output=True, text=True, timeout=30)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_the_deployment_passes_only_when_readiness_passes():
    r = _run_block(0)
    assert r.returncode == 0
    assert "PASS  database reachable (the app reports db true)" in r.stdout and "REACHED THE NEXT STEP" in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("code", [1, 2, 3, 99])       # not ready, bad answer, check error, docker itself failed
def test_the_deployment_fails_when_the_app_answers_but_the_database_does_not(code):
    r = _run_block(code)
    assert r.returncode == 1
    assert "FAIL  database not available: the app answers but the database does not" in r.stdout
    assert "REACHED THE NEXT STEP" not in r.stdout and "PASS" not in r.stdout
