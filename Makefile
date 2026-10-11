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

# Where `package` puts the built files.
DIST ?= dist

# Build date inside the package: the last commit, not the wall clock. A build
# of a tree with uncommitted changes carries the date of the commit below it.
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)
export SOURCE_DATE_EPOCH

# The steps depend on each other in the order written; nothing here gains
# from running side by side.
.NOTPARALLEL:

.PHONY: verify-all verify-engine-pin verify fetch environment verify-static package verify-reproducible verify-python lock clean

# The one gate is `make verify-all`: CI runs exactly that target, so a local
# green run and a green status check mean the same thing. It runs this
# repository's own checks first and then the standards engine: the agent
# contexts equal AGENTS.md, the register of the configured texts, the audit.
#
# The engine is the commit that PRAETOR_REF in .github/workflows/ci.yml pins.
# The file included in line 3 asks the hook launcher for that binary, so the
# gate and the Git hooks run the same engine. Without a pin the launcher runs
# whatever engine is on PATH and says so in one line only;
# `verify-engine-pin` turns that into a failure before anything else runs.
# The line below serves only a tree without the launcher.
PRAETORCTL ?= $(shell command -v praetorctl 2>/dev/null || command -v standardsctl 2>/dev/null || echo praetorctl)
verify-all: verify-engine-pin verify
	@$(PRAETORCTL) compile-context --verify
	@$(PRAETORCTL) caveman check --configured-sources
	@$(PRAETORCTL) audit

# The pattern is the one the launcher reads the pin with.
verify-engine-pin:
	@grep -Eqs '^[[:space:]]*PRAETOR_REF[[:space:]]*[:=]' .github/workflows/*.yml .github/workflows/*.yaml || { \
		printf '%s\n' 'FAIL: no workflow declares PRAETOR_REF, so no engine is pinned.' >&2; exit 1; }

# This repository's own checks; they need no engine. `fetch` is the only step
# that reaches the package index; everything after it runs without a network.
verify: fetch verify-static package verify-reproducible
	@set -e; for python in $(PYTHONS); do \
		$(MAKE) --no-print-directory verify-python PYTHON="$$python"; \
	done
	@printf '%s\n' 'PASS: format, lint, types, licence headers, exception list, package, same bytes from a second build, and its tests on $(PYTHONS).'

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
# backend on the first interpreter. Every interpreter then tests this one
# wheel. The hashes printed last are the ones a consumer pins.
#
# The bytes depend on the commit only. The backend writes the clock, the
# owner and the mode of each file into the source distribution, so it is
# rewritten with all of that fixed and stored uncompressed. The wheel is built
# from the rewritten file (a file missing there fails here), under a fixed
# file mode mask, and stores its members uncompressed (setup.cfg): two
# compression libraries give two outputs for the same input.
package: environment
	rm -rf '$(DIST)'
	umask 022 && '$(ENV)/bin/python' -m build --sdist --no-isolation --outdir '$(DIST)'
	'$(ENV)/bin/python' tools/reproducible_sdist.py '$(DIST)'/*.tar.gz
	umask 022 && '$(ENV)/bin/python' -m build --wheel --no-isolation --outdir '$(DIST)' '$(DIST)'/*.tar.gz
	sha256sum '$(DIST)'/*

# The same commit gives the same bytes: every interpreter builds the package
# once more, later and under another file mode mask of the caller, and a
# single differing byte fails. The report names the member and the field.
# The interpreters differ in their compression library on some hosts, which
# is the difference this check was first seen failing on.
verify-reproducible:
	@set -e; for python in $(PYTHONS); do \
		again="build/again/$$(basename "$$python")"; \
		( umask 077; $(MAKE) --no-print-directory package PYTHON="$$python" DIST="$$again" ); \
		'$(ENV)/bin/python' tools/compare_packages.py dist "$$again"; \
	done

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
