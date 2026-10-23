#!/usr/bin/env bash
# Дождаться готовности MLflow (после пробуждения WSL контейнеры стартуют несколько секунд).
for i in $(seq 40); do
  curl -sf -m 3 http://127.0.0.1:5006/health >/dev/null && exit 0
  sleep 3
done
echo "MLflow не отвечает" >&2; exit 1
