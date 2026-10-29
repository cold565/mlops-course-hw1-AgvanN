# Быстрые проверки работают без весов и без torch: uv sync --frozen (без --extra model).
PYTHON ?= .venv/bin/python
HW67 ?= $(firstword $(wildcard ../hw6-7) ../../mlops26-hw6-7/hw67-broken)

.PHONY: check check-public check-integration compile red-green install install-model \
        lab-demo serve-stub release-model serve-model smoke-model bench

install:
	uv sync --frozen
install-model:
	uv sync --frozen --extra model

compile:
	$(PYTHON) -m compileall -q release.py ledger.py artifacts.py flow.py lab.py api.py backends.py service.py model_runtime.py model_flow.py scripts tests tests_integration

check-public:
	$(PYTHON) -m unittest discover -s tests -v

check-integration:
	$(PYTHON) -m unittest discover -s tests_integration -t . -v

# Один быстрый запуск: 13 открытых тестов + 7 интеграционных (HTTP, subprocess, STUB).
check: compile check-public check-integration

# Новые тесты против исходного коммита (красный) и текущего кода (зелёный) -> evidence/
red-green:
	bash scripts/red_green.sh

lab-demo:
	bash scripts/lab_demo.sh

serve-stub:
	$(PYTHON) service.py --workspace .lab --port 8091 --warmup-seconds 10

# Реальная модель (нужен install-model и локальный кеш HF): flow -> gate -> accepted.json
release-model:
	HW67_ROOT=$(HW67) $(PYTHON) model_flow.py --role baseline

serve-model:
	$(PYTHON) service.py --backend transformers --pointer release/accepted.json --port 8092 --warmup-seconds 0

smoke-model:
	$(PYTHON) scripts/smoke.py --url http://127.0.0.1:8092

bench:
	$(PYTHON) scripts/bench.py --url http://127.0.0.1:8092 --out evidence/bench
