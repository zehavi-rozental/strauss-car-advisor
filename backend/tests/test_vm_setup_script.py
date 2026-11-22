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
# 22/11: exactly Docker's "(healthy)" (a bare "healthy" also matched "(unhealthy)"), read to the end (`grep -q` would
# quit early and, under `set -o pipefail`, a writer still writing gets SIGPIPE: a match reported as a failure)
LIVE_CHECK = "grep -F '(healthy)' >/dev/null"
WAIT_LOOP = "for i in $(seq 1 60); do docker compose ps app | "
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
    liveness = _index(f'{LIVE_CHECK} && ok "app live')
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
    live_line = _lines()[_index(f'{LIVE_CHECK} && ok "app live')]
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


# ---------- the liveness wait (lines 23-24, 22/11): only Docker's "(healthy)" passes ----------

HEADER = "NAME                 IMAGE         COMMAND                  SERVICE   CREATED         STATUS                     PORTS"


def _liveness_lines() -> str:
    """The wait loop and the liveness check, exactly as the script has them (two consecutive lines)."""
    lines = _lines()
    loop, check = _index(WAIT_LOOP), _index(f'{LIVE_CHECK} && ok "app live')
    assert check == loop + 1
    return "\n".join(lines[loop:check + 1])


def _run_liveness(tmp_path, statuses: list[str], lines: str | None = None) -> tuple[subprocess.CompletedProcess, int, int]:
    """Runs lines 23-24 with `docker compose ps app` answering the scripted STATUS values in turn (the last repeats),
    `sleep` doing nothing. Special values: __FAIL__ (ps fails, no output), __HEADER__ (no container), __BIG__ (a very
    long answer that says (healthy)). Returns the run, the number of `ps` calls and of `logs` calls."""
    (tmp_path / "statuses").write_text("\n".join(statuses) + "\n", encoding="utf-8")
    (tmp_path / "count").write_text("0", encoding="utf-8")
    (tmp_path / "logs").write_text("", encoding="utf-8")
    program = "\n".join([
        "set -uo pipefail",
        "ok() { printf 'PASS  %s\\n' \"$*\"; }; bad() { printf 'FAIL  %s\\n' \"$*\"; }",
        "sleep() { :; }",
        f"HDR='{HEADER}'",
        "docker() {",
        '  case "$*" in',
        '    "compose ps app")',
        '      n=$(( $(cat "$STATE/count") + 1 )); echo "$n" > "$STATE/count"',
        '      s=$(sed -n "${n}p" "$STATE/statuses"); [ -n "$s" ] || s=$(tail -n 1 "$STATE/statuses")',
        '      case "$s" in',
        '        __FAIL__) return 1 ;;',
        '        __HEADER__) echo "$HDR" ;;',
        "        __BIG__) echo \"$HDR\"; seq 1 200000 | sed 's/$/ strauss-edge-app-1 Up 3 minutes (healthy)/' ;;",
        '        *) echo "$HDR"; echo "strauss-edge-app-1   strauss-app   \\"python deploy/rende…\\"   app   3 minutes ago   $s   10000/tcp" ;;',
        '      esac ;;',
        '    "compose logs --tail 30 app")',
        '      echo logs >> "$STATE/logs"',
        "      printf '%s\\n' 'connecting to postgresql://u:p@h/db' 'token=abc123 refreshed' 'endpoint ep-cool-darkness-123 ready' ;;",
        '    *) echo "unexpected docker call: $*"; return 99 ;;',
        '  esac',
        "}",
        lines if lines is not None else _liveness_lines(),
        'echo "REACHED THE NEXT STEP"',
    ])
    state = str(tmp_path).replace("\\", "/")
    r = subprocess.run([BASH, "-c", f'STATE="{state}"\n' + program], capture_output=True, text=True, timeout=120)
    ps_calls = int((tmp_path / "count").read_text(encoding="utf-8").strip())
    log_calls = (tmp_path / "logs").read_text(encoding="utf-8").count("logs")
    return r, ps_calls, log_calls


def _passed(r) -> bool:
    return (r.returncode == 0 and "PASS  app live" in r.stdout and "REACHED THE NEXT STEP" in r.stdout
            and "FAIL" not in r.stdout)


def _failed(r) -> bool:
    return (r.returncode == 1 and "FAIL  app not live" in r.stdout and "REACHED THE NEXT STEP" not in r.stdout
            and "PASS" not in r.stdout)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h1_healthy_passes_at_once(tmp_path):
    r, ps, logs = _run_liveness(tmp_path, ["Up 3 minutes (healthy)"])
    assert _passed(r) and ps == 2 and logs == 0                      # the loop stops on its first look, then the check


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h2_unhealthy_never_passes(tmp_path):
    r, ps, logs = _run_liveness(tmp_path, ["Up 3 minutes (unhealthy)"])
    assert _failed(r) and ps == 61 and logs == 1                     # the whole wait (60), then the check


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h3_starting_then_healthy_passes(tmp_path):
    r, ps, _ = _run_liveness(tmp_path, ["Up 10 seconds (health: starting)"] * 3 + ["Up 2 minutes (healthy)"])
    assert _passed(r) and ps == 5                                    # stops on the 4th look, then the check


@pytest.mark.skipif(BASH is None, reason="needs bash")
@pytest.mark.parametrize("status", [
    "Up 10 seconds (health: starting)",                              # H4: never got healthy
    "Up 3 minutes",                                                  # H5: no health check at all
    "Restarting (1) 5 seconds ago",                                  # H6
    "Exited (1) 2 seconds ago",                                      # H6
    "__HEADER__",                                                    # H7: no container
    "__FAIL__",                                                      # H8: `docker compose ps` itself fails
])
def test_h4_to_h8_anything_but_healthy_fails(tmp_path, status):
    r, ps, logs = _run_liveness(tmp_path, [status])
    assert _failed(r) and ps == 61 and logs == 1, status


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h9_a_long_answer_with_healthy_passes_under_pipefail(tmp_path):
    r, _, _ = _run_liveness(tmp_path, ["__BIG__"])
    assert _passed(r)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h9_why_not_grep_q_it_reports_a_match_as_a_failure_under_pipefail(tmp_path):
    # the same lines with `grep -qF`: grep quits at the first match, the writer gets SIGPIPE, pipefail fails the line
    quitting = _liveness_lines().replace(LIVE_CHECK, "grep -qF '(healthy)'")
    r, _, _ = _run_liveness(tmp_path, ["__BIG__"], lines=quitting)
    assert not _passed(r)


def test_h10_only_the_exact_healthy_status_is_matched():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "grep -q healthy" not in text and "grep healthy" not in text
    ps_lines = [line for line in _lines() if "docker compose ps app |" in line]
    assert len(ps_lines) == 2 and all(LIVE_CHECK in line for line in ps_lines)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_h11_the_failure_logs_are_shown_without_secrets(tmp_path):
    r, _, logs = _run_liveness(tmp_path, ["Up 3 minutes (unhealthy)"])
    assert _failed(r) and logs == 1
    for leaked in ("u:p@h", "postgresql://", "token=abc123", "ep-cool-darkness"):
        assert leaked not in r.stdout, leaked
    assert "<db-url>" in r.stdout and "<neon-endpoint>" in r.stdout   # masked, not dropped


# ---------- 22/11: the self-test checks the path users take, sees the mail settings, and reports the engine ----------

def test_the_end_to_end_run_goes_through_the_public_link():
    # the VM cannot reach its own public address on 443 (Cloudflare's ranges only): https://$DOMAIN timed out there
    text = SCRIPT.read_text(encoding="utf-8")
    assert 'PUBLIC_URL="https://strauss-drive.strauss-drive-edge.workers.dev"' in text
    line = _lines()[_index("python /tmp/e2e_api.py")]
    assert '"$PUBLIC_URL"' in line and "$DOMAIN" not in line


def test_the_mail_check_sees_the_app_package():
    line = _lines()[_index("python /tmp/mail_check.py")]
    assert "-e PYTHONPATH=/srv/backend" in line


def _run_engine(tmp_path, exit_code: int) -> subprocess.CompletedProcess:
    lines = _lines()
    start = _index("engine=$(docker compose exec -T app python -m app.verify")
    block = "\n".join(lines[start:start + 2])
    program = "\n".join([
        "set -uo pipefail",
        "ok() { printf 'PASS  %s\\n' \"$*\"; }; bad() { printf 'FAIL  %s\\n' \"$*\"; }",
        "docker() {",
        '  [ "$*" = "compose exec -T app python -m app.verify" ] || { echo "unexpected docker call: $*"; return 99; }',
        "  printf 'checking\\n12/12 ערכי נטו תואמים לשטראוס\\n'",
        f"  return {exit_code}",
        "}",
        block,
    ])
    return subprocess.run([BASH, "-c", program], capture_output=True, text=True, encoding="utf-8", timeout=30)


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_the_engine_line_is_a_pass_when_every_value_matches(tmp_path):
    r = _run_engine(tmp_path, 0)
    assert "PASS  engine: 12/12" in r.stdout and "FAIL" not in r.stdout


@pytest.mark.skipif(BASH is None, reason="needs bash")
def test_the_engine_line_is_a_fail_when_app_verify_fails(tmp_path):
    r = _run_engine(tmp_path, 1)
    assert "FAIL  engine:" in r.stdout and "PASS" not in r.stdout
