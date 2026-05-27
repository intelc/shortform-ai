.PHONY: help install-dev test test-cli check-release-cli release-cli build clean

PYTHON ?= python3.12
DIST_DIR ?= dist/shortform-ai

help:
	@echo "shortform-ai — available commands"
	@echo ""
	@echo "  make install-dev       - Install package and build tooling into PYTHON"
	@echo "  make test              - Run CLI/runtime tests"
	@echo "  make check-release-cli - Scan source for accidental secrets"
	@echo "  make release-cli       - Test, scan, and build local release artifacts"
	@echo "  make build             - Build wheel and sdist"
	@echo "  make clean             - Remove local build/test artifacts"

install-dev:
	@"$(PYTHON)" -m pip install -e . build

test: test-cli

test-cli:
	@PYTHONPATH=. "$(PYTHON)" -m unittest discover tests
	@PYTHONPATH=. "$(PYTHON)" -m content_ai_runtime.cli --version >/dev/null
	@PYTHONPATH=. "$(PYTHON)" -m shortform_ai.cli --version >/dev/null

check-release-cli:
	@"$(PYTHON)" scripts/check_shortform_release.py

release-cli:
	@PYTHON_BIN="$(PYTHON)" bash scripts/release_shortform_ai.sh

build:
	@rm -rf "$(DIST_DIR)"
	@mkdir -p "$(DIST_DIR)"
	@"$(PYTHON)" -m build . --outdir "$(DIST_DIR)"

clean:
	@rm -rf build dist *.egg-info .pytest_cache
	@find . -type d -name __pycache__ -prune -exec rm -rf {} +
	@find . -type f -name '*.pyc' -delete
