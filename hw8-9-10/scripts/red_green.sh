#!/usr/bin/env bash
# Красный/зелёный прогон: одни и те же новые тесты против исходного коммита и текущего кода.
# Использование: bash scripts/red_green.sh [ORIGINAL_COMMIT]
set -u
cd "$(dirname "$0")/.."
ORIG=${1:-$(git rev-list --max-parents=0 HEAD)}
PY=${PYTHON:-.venv/bin/python}
OUT=evidence
mkdir -p "$OUT"
SRC=$(mktemp -d -t hw8910-original-XXXX)
git archive "$ORIG" | tar -x -C "$SRC"
{
  echo "# Исходный прототип: commit $ORIG -> $SRC"
  echo "# Тесты: tests_integration из $(git rev-parse --short HEAD) (+ рабочее дерево)"
  echo "# Команда: HW8910_TARGET=$SRC $PY -m unittest discover -s tests_integration -t . -v"
} > "$OUT/red-original.log"
HW8910_TARGET="$SRC" "$PY" -m unittest discover -s tests_integration -t . -v >> "$OUT/red-original.log" 2>&1
echo "exit_code=$?" >> "$OUT/red-original.log"
{
  echo "# Исправленная версия: $(git rev-parse --short HEAD) (+ рабочее дерево)"
  echo "# Команда: $PY -m unittest discover -s tests_integration -t . -v"
} > "$OUT/green-fixed.log"
"$PY" -m unittest discover -s tests_integration -t . -v >> "$OUT/green-fixed.log" 2>&1
echo "exit_code=$?" >> "$OUT/green-fixed.log"
# Открытые 13 тестов на исходной версии (для сравнения)
(cd "$SRC" && "$OLDPWD/$PY" -m unittest discover -s tests -v) > "$OUT/public-tests-original.log" 2>&1
echo "exit_code=$?" >> "$OUT/public-tests-original.log"
rm -rf "$SRC"
grep -E "^(test_|Ran|OK|FAILED|exit_code)|\.\.\. (ok|FAIL|ERROR)" "$OUT/red-original.log" "$OUT/green-fixed.log" | sed 's/(tests_integration[^)]*)//'
