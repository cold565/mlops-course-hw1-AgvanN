#!/usr/bin/env bash
# Быстрые проверки: зелёный прогон -> намеренная поломка (ненулевой exit) -> восстановление.
# Поломка: API перестаёт ограничивать max_new_tokens сверху лимитом протокола (регрессия дефекта 7).
set -u
cd "$(dirname "$0")/.."
OUT=evidence; mkdir -p "$OUT"
git diff --quiet -- api.py || { echo "api.py имеет незакоммиченные изменения — стоп"; exit 1; }
{ echo "# make check на $(git rev-parse --short HEAD)"; make check; echo "exit_code=$?"; } > "$OUT/check-green.log" 2>&1
sed -i.bak 's/if not 1 <= limit <= backend.max_new_tokens:/if not 1 <= limit:  # НАМЕРЕННАЯ ПОЛОМКА/' api.py
{ echo "# make check с намеренной поломкой:"; git diff -- api.py; make check; echo "exit_code=$?"; } > "$OUT/check-broken.log" 2>&1
mv api.py.bak api.py
{ echo "# восстановлено: git diff -- api.py (пусто = как в коммите)"; git diff --stat -- api.py; echo "diff_exit=$?"; } >> "$OUT/check-broken.log"
grep -E "^(Ran|OK|FAILED|exit_code|diff_exit)" "$OUT/check-green.log" "$OUT/check-broken.log"
