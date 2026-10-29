#!/usr/bin/env bash
# Реальная модель: сбой/восстановление, повтор, кандидат без весов, изменение данных оценки.
# Логи -> evidence/model/. Нужны: uv sync --extra model, кеш HF Qwen/Qwen3-0.6B@c1899de…, ДЗ 6+7 рядом.
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
L=evidence/model; mkdir -p "$L"
HW67=${HW67_ROOT:-../../mlops26-hw6-7/hw67-broken}
ptr() { [ -f release/accepted.json ] && shasum -a 256 release/accepted.json | cut -c1-64 || echo none; }
pubs() { [ -f release/publications.jsonl ] && wc -l < release/publications.jsonl | tr -d ' ' || echo 0; }
step() { # name, cmd...
  local name=$1; shift
  { echo "\$ $*"; echo "accepted.json sha256 before: $(ptr)"; "$@"; echo "exit=$?";
    echo "accepted.json sha256 after: $(ptr)"; echo "publication events: $(pubs)"; } > "$L/$name.log" 2>&1
  grep -E "^\\\$|PREPARED|CACHE_HIT|EVAL_CACHE|EVALUATED|GATE|PUBLISHED|RECOVERED|REJECTED|INJECTED|exit=|sha256 (before|after)|publication events" "$L/$name.log" | cut -c1-220
  echo
}
step 01-fail-after-prepare "$PY" model_flow.py --role baseline --fail-after-prepare
step 02-recover-full-run   "$PY" model_flow.py --role baseline
step 03-repeat-release     "$PY" model_flow.py --role baseline
step 04-candidate-unavailable "$PY" model_flow.py --role candidate
COPY=$(mktemp -d -t hw67-changed-XXXX)
rsync -a --exclude .venv --exclude .git --exclude .dvc --exclude data/tokenized --exclude data/raw --exclude __pycache__ "$HW67/" "$COPY/"
"$PY" - "$COPY/data/eval/golden.jsonl" <<'PYEOF'
import sys
p = sys.argv[1]; t = open(p, encoding='utf-8').read()
old, new = 'Which is the longest river in South America?', 'Which river is the longest one in South America?'
assert old in t
open(p, 'w', encoding='utf-8').write(t.replace(old, new, 1))
PYEOF
echo "# копия ДЗ 6+7 с изменённым geo-02 в data/eval/golden.jsonl (тот же путь): $COPY" > "$L/05-data-change.note"
step 05-data-change env HW67_ROOT="$COPY" "$PY" model_flow.py --role baseline
step 06-after-data-change-original "$PY" model_flow.py --role baseline
rm -rf "$COPY"
