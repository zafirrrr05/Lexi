PY := .venv/bin/python
LEXI := .venv/bin/lexi

.PHONY: venv bootstrap serve twin certify test bench topology lint typecheck dataset

venv:
	python3 -m venv .venv
	$(PY) -m pip install -q --upgrade pip
	$(PY) -m pip install -q -r requirements.txt -e .

dataset:
	$(PY) scripts/make_dataset.py

bootstrap: venv dataset

serve:
	$(LEXI) serve

twin:
	$(LEXI) twin

certify:
	$(LEXI) certify

bench:
	$(LEXI) bench

test:
	$(PY) -m pytest -q

topology:
	$(PY) scripts/check_topology.py

lint:
	.venv/bin/ruff check src tests scripts

typecheck:
	.venv/bin/mypy
