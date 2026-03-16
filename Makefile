BUILD_DIR := _build
PREFIX    := $(HOME)/.local
VENV      := .venv
PYTHON    := $(VENV)/bin/python3

.PHONY: run build install uninstall clean deps apt-deps lint format

$(VENV):
	python3 -m venv --system-site-packages $(VENV)
	$(PYTHON) -m ensurepip --upgrade

apt-deps:
	sudo apt install -y gir1.2-gtksource-5

deps: $(VENV)
	$(PYTHON) -m pip install psycopg[binary] keyring paramiko

run: $(VENV)
	$(PYTHON) run.py

build:
	meson setup $(BUILD_DIR) --prefix=$(PREFIX)

install: build
	meson install -C $(BUILD_DIR)

uninstall:
	meson --internal uninstall -C $(BUILD_DIR)

clean:
	rm -rf $(BUILD_DIR) src/__pycache__ __pycache__

lint: $(VENV)
	$(PYTHON) -m ruff check src/

format: $(VENV)
	$(PYTHON) -m ruff format src/
