# MLOps-26 (МГТУ) — домашние работы

Каждое ДЗ — отдельная папка со своим `pyproject.toml`, `uv.lock`, `Makefile`
и `README.md` (как запускать). Данные — под DVC, в Git только `.dvc`-файлы.
Веса моделей, тензоры, видео и `.venv` в репозиторий не входят.

| ДЗ | Папка | Тема | Главный отчёт |
|---|---|---|---|
| 1 | [hw1](hw1/) | Запуск Qwen3-0.6B, замер скорости генерации, конфиг вместо хардкода | [docs/hardware.md](hw1/docs/hardware.md) |
| 2 | [hw2](hw2/) | Анатомия модели: параметры, слои, активации | [docs/anatomy.md](hw2/docs/anatomy.md) |
| 3 | [hw3](hw3/) | Датасет: DVC-пайплайн, очистка, дедупликация, PII, сплит, контаминация | [docs/datasheet.md](hw3/docs/datasheet.md), [docs/defects.md](hw3/docs/defects.md) |
| 4 | [hw4](hw4/) | Токенизация: маска лосса по промпту, left padding, обрезка, packing | [docs/defects.md](hw4/docs/defects.md) |
| 5 | [hw5](hw5/) | LoRA-дообучение, сравнение вариантов, кривые обучения | [docs/compare.md](hw5/docs/compare.md), [docs/defects.md](hw5/docs/defects.md) |
| 6+7 | [hw6-7](hw6-7/) | MLflow: 6 LoRA-runs, реестр, хранение; оценка ответов, golden set, quality gate | [docs/conclusion.md](hw6-7/docs/conclusion.md), [docs/defects.md](hw6-7/docs/defects.md) |

Модель во всех работах — `Qwen/Qwen3-0.6B`. Окружение: Python 3.12+, uv;
для ДЗ 6+7 — Docker Compose (MLflow + Postgres), запуск на Windows через WSL
(см. [hw6-7/docs/SUBMISSION.md](hw6-7/docs/SUBMISSION.md)).
