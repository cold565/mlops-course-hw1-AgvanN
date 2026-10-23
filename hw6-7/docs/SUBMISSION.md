# Сдача ДЗ 6+7: доступ и воспроизведение

## Что где

| Материал | Путь |
|---|---|
| Сравнение 6 runs (из MLflow) | `docs/comparison.csv` |
| Разбор 4 дефектов | `docs/defects.md` |
| Единый вывод A+B | `docs/conclusion.md` |
| Отчёты оценки (копия `metrics/`, т.к. `metrics/` в .gitignore) | `reports/` |
| Итог `make check-all` | `reports/check-all.txt` |
| Протокол и baseline | `evaluation/params.json`, `evaluation/baseline.json` |
| Код оценки | `evaluation/solution.py` (`score`, `quality_gate`) |
| Данные проверки | `data/eval` под DVC (стадия `eval_data` в `dvc.yaml`) |

`reports/`: `baseline-outputs.json`, `candidate-outputs.json` (сырые ответы),
`baseline-eval.json`, `candidate-eval.json`, `eval-diff.json`, `quality-gate.json`,
`negative-gate.json`, `storage-check.json`, `released.json`, `candidate-model.json`,
`sweep.json`.

## Данные (DVC)

- `data/raw` — сырые train/val ДЗ 4 (`train.jsonl` — источник индекса утечки).
- `data/tokenized` — тензоры ДЗ 4 (вход обучения, SHA256 в тегах runs).
- `data/eval` — golden (50), behavioral (3), training-index (1546);
  строится `dvc repro eval_data` из `scripts/build_eval_data.py` и `data/raw/train.jsonl`.

Remote — локальный каталог `../../dvc-storage` (относительно репозитория).
Для проверки преподавателем: приложить этот каталог архивом или выполнить
`dvc remote modify local url <путь>` и `dvc pull`. Golden также
воспроизводится без remote: `uv run python scripts/build_eval_data.py`.

## Запуск на Windows

Всё выполняется в WSL Ubuntu (Docker Engine, uv, GPU через CUDA):

```bash
wsl -d Ubuntu --cd <путь к hw67-broken> -- bash scripts/wsl.sh make <цель>
```

`scripts/wsl.sh` выставляет отдельное Linux-окружение uv
(`~/.venvs/mlops-hw67`), чтобы `.venv` не смешивался с Windows-Python.
`scripts/wait-mlflow.sh` ждёт готовности сервера после пробуждения WSL.
Порядок команд — как в README.

## Видео 5–8 минут (план)

1. MLflow → `hw6-lora` → sweep `088d1ea4…` и 6 дочерних runs, сравнение val_loss.
2. Models → `my-qwen-adapter` v1: aliases `candidate` и `champion`.
3. `hw67-evaluation` → run `0c5e59f3…`: метрики и артефакты `evaluation/`.
4. `make eval` (PASS) и `make check-negative` (отказ на испорченных ответах).
5. `make check-storage` и `make check-all`: 9/9, 24 теста, отпечаток `c42ac7788026`.
