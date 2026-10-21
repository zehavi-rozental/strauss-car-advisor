#!/usr/bin/env bash
# Basic secret scan of the tracked files: fails on an API key pattern or a tracked .env file.
# Prints the file and line number only, never the matched value.
set -euo pipefail

status=0
if git ls-files | grep -E '(^|/)\.env($|\.)' | grep -v '\.env\.example$'; then
  echo "::error::a .env file is tracked by git (secrets live only in backend/.env, which is git-ignored)"
  status=1
fi

# Google API keys, OpenAI / Groq / OpenRouter / Anthropic style keys, private keys, Render deploy hooks
pattern='AIza[0-9A-Za-z_-]{35}|sk-[A-Za-z0-9_-]{20,}|gsk_[A-Za-z0-9]{20,}|sk-or-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|api\.render\.com/deploy/srv-[a-z0-9]+\?key='
hits=$(git ls-files -z | xargs -0 grep -IlE "$pattern" -- 2>/dev/null || true)
if [ -n "$hits" ]; then
  for f in $hits; do
    lines=$(grep -nE "$pattern" -- "$f" | cut -d: -f1 | tr '\n' ' ')
    echo "::error file=$f::possible secret on line(s) $lines"
  done
  status=1
fi

[ $status -eq 0 ] && echo "secret scan: no secrets found in tracked files"
exit $status
