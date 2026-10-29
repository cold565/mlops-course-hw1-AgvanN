#!/usr/bin/env bash
# Опыты INCIDENTS.txt на стенде STUB ONLY (исправленная версия). Логи -> evidence/lab/.
# Каждый независимый опыт — в новом workspace.
set -u
cd "$(dirname "$0")/.."
PY=${PYTHON:-.venv/bin/python}
OUT=evidence/lab; mkdir -p "$OUT"
BASE=$(mktemp -d -t hw8910-lab-XXXX)
sha() { shasum -a 256 "$1/accepted.json" | cut -c1-64; }
events() { [ -f "$1/publications.jsonl" ] && grep -c . "$1/publications.jsonl" || echo 0; }
run() { echo "\$ lab.py $*"; "$PY" lab.py "$@" 2>&1 | grep -E "^(PREPARED|CACHE_HIT|PUBLISHED|ALREADY_PUBLISHED|RECOVERED|REJECTED|INJECTED|ROLLBACK_TO|Создан)|Finished in state"; echo "exit=${PIPESTATUS[0]}"; }

{
echo "## 1. Сбой после подготовки -> восстановление в новом процессе -> изменение входа"
W=$BASE/w1; run --workspace $W init
run --workspace $W run --fail-after-prepare
run --workspace $W run
printf 'Изменённый пример\n' > $W/input.txt; echo "# input.txt изменён по тому же пути"
run --workspace $W run
"$PY" - "$W/config.json" <<'EOF'
import json,sys; p=sys.argv[1]; c=json.load(open(p)); c['max_length']=256; json.dump(c,open(p,'w'))
EOF
echo "# config.json: max_length 128 -> 256"; run --workspace $W run
echo "# attempts.jsonl (состояния задач Prefect по попыткам):"
"$PY" -c "import json,sys; [print(json.dumps({k:a.get(k) for k in ('flow_run_id','outcome','tasks')},ensure_ascii=False)) for a in map(json.loads,open('$W/attempts.jsonl'))]"

echo; echo "## 2-3. Неполный, чужой (A для B), отклонённый отчёт и NaN"
W=$BASE/w2; run --workspace $W init
"$PY" -c "import json;r=json.load(open('$W/report-B.json'));r['metrics']['accuracy']=float('nan');json.dump(r,open('$W/report-nan.json','w'))"
echo "# report-nan.json: копия report-B с metrics.accuracy = NaN"
echo "accepted.json sha256 before: $(sha $W)"
for r in report-incomplete.json report-A.json report-reject.json report-nan.json; do run --workspace $W run --report $r; echo "accepted.json sha256 after $r: $(sha $W)"; done

echo; echo "## 4. Повтор выпуска B в двух процессах"
W=$BASE/w4; run --workspace $W init
run --workspace $W run; run --workspace $W run
echo "события B в publications.jsonl: $(grep -c fixture-B $W/publications.jsonl)"

echo; echo "## 4b. Сбой между записью указателя и журнала -> повтор восстанавливает событие один раз"
W=$BASE/w4b; run --workspace $W init
run --workspace $W run --fail-after-pointer
echo "указатель: $("$PY" -c "import json;print(json.load(open('$W/accepted.json'))['model_id'])"), события B: $(grep -c fixture-B $W/publications.jsonl)"
run --workspace $W run; run --workspace $W run
echo "события B: $(grep -c fixture-B $W/publications.jsonl)"; tail -1 $W/publications.jsonl

echo; echo "## 5-7. Сервис: готовность, фактическая версия A -> B -> откат A, неверные запросы"
W=$BASE/w6; run --workspace $W init
PORT=8193; U=http://127.0.0.1:$PORT
start() { "$PY" service.py --workspace $W --port $PORT --warmup-seconds ${1:-0} > $BASE/svc.log 2>&1 & SVC=$!; until curl -s -o /dev/null $U/health; do sleep 0.1; done; }
stop() { kill $SVC; wait $SVC 2>/dev/null; }
ASK='{"prompt":"version?","max_new_tokens":16}'
gen() { curl -s -X POST $U/generate -H 'Content-Type: application/json' -d "$ASK"; }
start 5
echo "сразу после старта: health=$(curl -s -o /dev/null -w %{http_code} $U/health) ready=$(curl -s -o /dev/null -w %{http_code} $U/ready) generate=$(curl -s -o /dev/null -w %{http_code} -X POST $U/generate -H 'Content-Type: application/json' -d '{"prompt":"x"}') debug=$(curl -s $U/debug)"
until [ "$(curl -s -o /dev/null -w %{http_code} $U/ready)" = 200 ]; do sleep 0.2; done
echo "после прогрева: ready=$(curl -s $U/ready)"; echo "debug: $(curl -s $U/debug)"
echo "calls до неверных запросов: $(curl -s $U/debug)"
for b in '{"prompt":""}' '{"prompt":"x","max_new_tokens":0}' '{"prompt":"x","max_new_tokens":100000}' '{"prompt":"x","max_new_tokens":"many"}'; do
  echo "POST $b -> $(curl -s -o /dev/null -w %{http_code} -X POST $U/generate -H 'Content-Type: application/json' -d "$b")"; done
echo "calls после неверных запросов: $(curl -s $U/debug)"
echo "валидный: $(gen)"
run --workspace $W run
echo "accepted.json теперь: $("$PY" -c "import json;print(json.load(open('$W/accepted.json'))['model_id'])")"
echo "до перезапуска: $(gen)"
stop; start 0; until [ "$(curl -s -o /dev/null -w %{http_code} $U/ready)" = 200 ]; do sleep 0.1; done
echo "после перезапуска: $(gen)"
run --workspace $W rollback
stop; start 0; until [ "$(curl -s -o /dev/null -w %{http_code} $U/ready)" = 200 ]; do sleep 0.1; done
echo "после отката и перезапуска: $(gen)"
stop
echo "# publications.jsonl:"; "$PY" -c "import json; [print(json.dumps({k:e[k] for k in ('event','model_id','release_id','previous_release_id','recovered')})) for e in map(json.loads,open('$W/publications.jsonl'))]"
} > "$OUT/lab-demo.log" 2>&1
rm -rf "$BASE"
cat "$OUT/lab-demo.log"
