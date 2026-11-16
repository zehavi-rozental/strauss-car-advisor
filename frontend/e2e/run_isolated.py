"""Browser tests against a server of their own: a fresh SQLite file, a throwaway rep code and mail key, fake mail in a
temporary folder, no model (unless --llm), all deleted at the end. Nothing from an earlier run can be read, and nothing
is written anywhere else (never a shared or a production database).

    python e2e/run_isolated.py [--llm] [--build] <playwright args...>     (from frontend/, the backend's venv python)
    e.g. python e2e/run_isolated.py e2e/journeys.spec.ts e2e/ui-polish-hotfix.spec.ts

The same server CI starts (deploy/render_start.py: migrations, the tax file, the built React app, uvicorn).
"""
from __future__ import annotations

import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[1]
BACKEND = FRONTEND.parent / "backend"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def main(argv: list[str]) -> int:
    llm = "--llm" in argv
    build = "--build" in argv
    args = [a for a in argv if a not in ("--llm", "--build")]
    npx = shutil.which("npx") or "npx"
    if build or not (FRONTEND / "dist" / "index.html").exists():
        subprocess.run([npx, "vite", "build"], cwd=FRONTEND, check=True)
    from cryptography.fernet import Fernet

    work = Path(tempfile.mkdtemp(prefix="e2e-isolated-"))
    port = free_port()
    rep_token = secrets.token_urlsafe(18)
    (work / "mail").mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DATABASE_URL", "EMAIL_", "REP_"))}
    env.update({
        "DATABASE_URL": f"sqlite:///{(work / 'e2e.db').as_posix()}",
        "PORT": str(port), "FRONTEND_DIST_DIR": str(FRONTEND / "dist"), "CONFIG_MODE": "strict",
        "LLM_MODE": "llm_first", "SESSION_COOKIE_SECURE": "false", "REP_ACCESS_TOKEN": rep_token,
        "PAYSLIP_ENABLED": "true", "PAYSLIP_FAKE_VISION_DIR": str(FRONTEND / "e2e" / "fixtures" / "payslips" / "vision"),
        "PAYSLIP_RATE_LIMIT_PER_MINUTE": "200", "CREATE_RATE_LIMIT_PER_MINUTE": "500",
        "EMAIL_BACKEND": "fake", "EMAIL_FAKE_NOTICE": "false", "EMAIL_FAKE_DIR": str(work / "mail"),
        "EMAIL_PUBLIC_APP_URL": f"http://localhost:{port}", "EMAIL_ENCRYPTION_KEY": Fernet.generate_key().decode(),
    })
    if not llm:
        env["LLM_CHAIN"] = ""                        # templates only: no model call, no cost
    log = open(work / "server.log", "w", encoding="utf-8")
    # render_start's own steps (migrations, the tax file), then uvicorn as a child of ours: render_start ends in
    # os.execvp, which on Windows starts a new process and exits, so it could not be stopped at the end
    prep = ("import runpy; m = runpy.run_path('deploy/render_start.py', run_name='prep'); "
            "m['migrate'](); m['load_tax_file_if_empty']()")
    if subprocess.run([sys.executable, "-c", prep], cwd=BACKEND, env=env, stdout=log, stderr=subprocess.STDOUT).returncode:
        log.close()
        print((work / "server.log").read_text(encoding="utf-8")[-3000:])
        shutil.rmtree(work, ignore_errors=True)
        return 1
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.serve_spa:create_render_app", "--factory",
                               "--host", "127.0.0.1", "--port", str(port)], cwd=BACKEND, env=env, stdout=log,
                              stderr=subprocess.STDOUT)
    try:
        base = f"http://localhost:{port}"
        for _ in range(180):
            try:
                urllib.request.urlopen(f"{base}/api/health", timeout=2)
                break
            except OSError:
                if server.poll() is not None:
                    break
                time.sleep(1)
        else:
            server.poll()
        if server.returncode is not None:
            log.close()
            print((work / "server.log").read_text(encoding="utf-8")[-3000:])
            return 1
        print(f"isolated server {base} (fresh database, {'model on' if llm else 'templates only'})", flush=True)
        run_env = {**os.environ, "E2E_BASE_URL": base, "REP_TOKEN": rep_token, "EMAIL_FAKE_DIR": str(work / "mail")}
        return subprocess.run([npx, "playwright", "test", "--reporter=list", *args], cwd=FRONTEND, env=run_env).returncode
    finally:
        server.terminate()
        try:
            server.wait(timeout=15)
        except subprocess.TimeoutExpired:
            server.kill()
        log.close()
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
