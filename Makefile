# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
-include .config/praetor/engine.mk

SHELL := /bin/sh

# Interpreters the gate runs on: the supported floor and every newer release.
# A name is looked up on PATH; a full path works as well. An interpreter that
# is missing fails the gate, it is never skipped.
PYTHONS ?= python3.12 python3.13 python3.14

# The interpreter of the targets that work on one version.
PYTHON ?= $(firstword $(PYTHONS))
ifeq ($(strip $(PYTHON)),)
$(error PYTHONS names no interpreter)
endif
ENV = .venv/$(notdir $(PYTHON))

# The licence check is not part of the lock (see requirements/dev.in).
REUSE ?= reuse

# Build date inside the wheel: the last commit, not the wall clock.
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)
export SOURCE_DATE_EPOCH

# The steps depend on each other in the order written; nothing here gains
# from running side by side.
.NOTPARALLEL:

.PHONY: verify-all verify fetch environment verify-static package verify-python lock clean

# The one gate is `make verify-all`: CI runs exactly that target, so a local
# green run and a green status check mean the same thing. It runs this
# repository's own checks first and then the standards engine: the agent
# contexts equal AGENTS.md, the register of the configured texts, the audit.
# Praetor ships one binary under two names; whichever is installed is used.
PRAETORCTL ?= $(shell command -v praetorctl 2>/dev/null || command -v standardsctl 2>/dev/null || echo praetorctl)
verify-all: verify
	@$(PRAETORCTL) compile-context --verify
	@$(PRAETORCTL) caveman check --configured-sources
	@$(PRAETORCTL) audit

# This repository's own checks; they need no engine. `fetch` is the only step
# that reaches the package index; everything after it runs without a network.
verify: fetch verify-static package
	@set -e; for python in $(PYTHONS); do \
		$(MAKE) --no-print-directory verify-python PYTHON="$$python"; \
	done
	@printf '%s\n' 'PASS: format, lint, types, licence headers, exception list, package, and its tests on $(PYTHONS).'

fetch:
	@set -e; for python in $(PYTHONS); do \
		$(MAKE) --no-print-directory environment PYTHON="$$python"; \
	done

# One environment per interpreter, holding exactly the packages of the lock:
# pip refuses a package whose hash the lock does not name, and installs no
# source distribution, so no build script of a tool runs here.
environment: $(ENV)/.locked
$(ENV)/.locked: requirements/dev.txt
	rm -rf '$(ENV)'
	'$(PYTHON)' -m venv '$(ENV)'
	'$(ENV)/bin/python' -m pip install --disable-pip-version-check --require-hashes \
		--only-binary=:all: -r requirements/dev.txt
	touch '$@'

# Checks that read the source and do not depend on the interpreter running the
# tests: once, on the first interpreter. The lint leaves out exactly the files
# ruff.toml names; the last step fails when one of them has no entry in
# exceptions.toml, or when an entry has expired or is no longer needed.
verify-static: environment
	'$(ENV)/bin/ruff' format --check .
	'$(ENV)/bin/ruff' check .
	'$(ENV)/bin/mypy'
	$(REUSE) lint
	'$(ENV)/bin/python' tools/check_exceptions.py

# One source distribution and one wheel, built once with the locked build
# backend on the first interpreter; the wheel is built from the source
# distribution, so a file missing there fails here. Every interpreter then
# tests this one wheel. The hash printed last is the one a consumer pins.
package: environment
	rm -rf dist
	'$(ENV)/bin/python' -m build --no-isolation --outdir dist
	sha256sum dist/*

# Per interpreter: install the wheel and run the tests against the installed
# package, not against the source tree. The interpreter is printed first so
# the log names what ran.
verify-python: environment
	'$(ENV)/bin/python' -VV
	'$(ENV)/bin/python' -m pip install --disable-pip-version-check --no-index --no-deps \
		--force-reinstall dist/*.whl
	'$(ENV)/bin/python' -I -c 'import plurano_python_library as p; print(p.__file__); assert "site-packages" in p.__file__'
	COVERAGE_FILE='$(ENV)/coverage' '$(ENV)/bin/python' -m coverage run -m pytest
	COVERAGE_FILE='$(ENV)/coverage' '$(ENV)/bin/python' -m coverage report

# Regenerates the lock from requirements/dev.in. A release younger than three
# days is not eligible.
lock:
	cd requirements && uv pip compile --universal --generate-hashes --python-version=3.12 \
		--exclude-newer="$$(date -u -d '3 days ago' +%Y-%m-%dT00:00:00Z)" \
		dev.in --output-file=dev.txt

clean:
	rm -rf .venv dist build src/*.egg-info .mypy_cache .ruff_cache .pytest_cache
