BUILD_DIR := _build
PREFIX    := $(HOME)/.local
VENV      := .venv
PYTHON    := $(VENV)/bin/python3

.PHONY: run build install uninstall clean deps apt-deps release release-deps lint format

$(VENV):
	python3 -m venv --system-site-packages $(VENV)
	$(PYTHON) -m ensurepip --upgrade

apt-deps:
	sudo apt install -y gir1.2-gtksource-5

release-deps:
	sudo apt install -y flatpak-builder rpm gh ruby ruby-dev build-essential
	sudo gem install fpm
	@echo "Also install appimagetool from https://github.com/AppImage/appimagetool/releases and put it in PATH"

release:
	@test -n "$(VERSION)" || (echo "Usage: make release VERSION=x.y.z"; exit 1)
	./scripts/release.sh $(VERSION)

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
