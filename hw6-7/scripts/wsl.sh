#!/usr/bin/env bash
# Запуск команды проекта из Windows через WSL: wsl -d Ubuntu -- bash scripts/wsl.sh <команда...>
set -euo pipefail
cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"
# .venv на NTFS не делим с Windows-Python: отдельное окружение Linux внутри WSL.
export UV_PROJECT_ENVIRONMENT="$HOME/.venvs/mlops-hw67"
export UV_LINK_MODE=copy
exec "$@"
