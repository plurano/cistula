# SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
# SPDX-License-Identifier: EUPL-1.2
-include .config/praetor/engine.mk

SHELL := /bin/sh

# Interpreters the gate runs on: the supported floor and every newer release.
# A name is looked up on PATH; a full path works as well. An interpreter that
# is missing fails the gate, it is never skipped.
PYTHONS ?= python3.11 python3.12 python3.13 python3.14 python3.15

# The interpreter of the targets that work on one version.
PYTHON ?= $(firstword $(PYTHONS))
ifeq ($(strip $(PYTHON)),)
$(error PYTHONS names no interpreter)
endif
ENV = .venv/$(notdir $(PYTHON))

# The licence check is not part of the lock (see requirements/dev.in).
REUSE ?= reuse

# The declared scanners (tools/scanners.py). semgrep comes from its own lock in
# an environment of its own; trivy is one static binary, taken from PATH here
# and installed in CI from its release archive pinned by digest.
SEMGREP_ENV = .venv/semgrep
SEMGREP ?= $(CURDIR)/$(SEMGREP_ENV)/bin/semgrep
TRIVY ?= trivy
export SEMGREP TRIVY

# Where `package` puts the built files.
DIST ?= dist

# Build date inside the package: the last commit, not the wall clock. A build
# of a tree with uncommitted changes carries the date of the commit below it.
SOURCE_DATE_EPOCH ?= $(shell git log -1 --format=%ct)
export SOURCE_DATE_EPOCH

# The steps depend on each other in the order written; nothing here gains
# from running side by side.
.NOTPARALLEL:

.PHONY: verify-all verify-engine-pin verify fetch environment semgrep-environment verify-static verify-scanners package verify-package-size verify-durable-figures verify-reproducible verify-python lock clean

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
# that reaches the network (the package index, and ghcr.io for the trivy
# database); everything after it runs offline.
verify: fetch verify-static verify-scanners package verify-package-size verify-durable-figures verify-reproducible
	@set -e; for python in $(PYTHONS); do \
		$(MAKE) --no-print-directory verify-python PYTHON="$$python"; \
	done
	@printf '%s\n' 'PASS: format, lint, types, licence headers, exception list, Python versions, semgrep and trivy scans, package, its size as the README states it, the durable-write figures the README states, same bytes from a second build, a consumer install by hash, and its tests on $(PYTHONS).'

fetch:
	@set -e; for python in $(PYTHONS); do \
		$(MAKE) --no-print-directory environment PYTHON="$$python"; \
	done
	@$(MAKE) --no-print-directory semgrep-environment
	'$(ENV)/bin/python' tools/scanners.py fetch-db

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

# semgrep's environment, from its own lock in the same way.
semgrep-environment: $(SEMGREP_ENV)/.locked
$(SEMGREP_ENV)/.locked: requirements/semgrep.txt
	rm -rf '$(SEMGREP_ENV)'
	'$(PYTHON)' -m venv '$(SEMGREP_ENV)'
	'$(SEMGREP_ENV)/bin/python' -m pip install --disable-pip-version-check --require-hashes \
		--only-binary=:all: -r requirements/semgrep.txt
	touch '$@'

# Checks that read the source and do not depend on the interpreter running the
# tests: once, on the first interpreter. The lint leaves out exactly the files
# ruff.toml names; the step after the licence check fails when one of them has
# no entry in exceptions.toml, or when an entry has expired or is no longer
# needed. The last step fails when the metadata, the lint, the type check, the
# lock, PYTHONS above or the CI workflow name different Python versions.
verify-static: environment
	'$(ENV)/bin/ruff' format --check .
	'$(ENV)/bin/ruff' check .
	'$(ENV)/bin/mypy'
	$(REUSE) lint
	'$(ENV)/bin/python' tools/check_exceptions.py
	'$(ENV)/bin/python' tools/check_python_versions.py

# The two declared scanners. Each selftest plants findings in a scratch
# directory first (every semgrep rule, one known pip advisory), so a scanner
# that stopped reporting fails before its scan of this tree is trusted. The
# semgrep rules are .config/semgrep/hiss.yml; trivy reads the locks under
# requirements/ against the database `fetch` downloaded by digest.
verify-scanners: environment
	'$(SEMGREP)' --version
	'$(TRIVY)' --version
	'$(ENV)/bin/python' tools/scanners.py selftest semgrep
	'$(ENV)/bin/python' tools/scanners.py check semgrep
	'$(ENV)/bin/python' tools/scanners.py selftest trivy
	'$(ENV)/bin/python' tools/scanners.py check trivy

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

# The size of the package, which the README states, is measured: a figure
# that is off by more than a tenth fails, so the sentence is kept current.
verify-package-size: environment
	'$(ENV)/bin/python' tools/check_package_size.py README.md '$(DIST)'

# The cost of a durable write, which the README states per file system, is
# measured here: on a disk, a default write that costs less than 4 times one
# without the flushes did not reach the disk, and a figure off by more than a
# factor of 4 for the file system measured is out of date
# (tools/check_durable_figures.py).
verify-durable-figures: environment
	rm -rf build/durable && mkdir -p build/durable
	PYTHONPATH=src '$(ENV)/bin/python' tools/measure_durable_write.py build/durable 50 > build/durable.tsv
	'$(ENV)/bin/python' tools/check_durable_figures.py README.md build/durable.tsv

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
# the log names what ran. Before the tests, the wheel is installed the way the
# README tells a consumer to (a new environment, the wheel pinned by its hash,
# nothing else in it) and tests/consumer.py calls the public interface there.
verify-python: environment
	'$(ENV)/bin/python' -VV
	'$(ENV)/bin/python' -m pip install --disable-pip-version-check --no-index --no-deps \
		--force-reinstall dist/*.whl
	'$(ENV)/bin/python' -I -c 'import cistula as p; print(p.__file__); assert "site-packages" in p.__file__'
	'$(ENV)/bin/python' tools/check_consumer.py dist tests/consumer.py
	COVERAGE_FILE='$(ENV)/coverage' '$(ENV)/bin/python' -m coverage run -m pytest
	COVERAGE_FILE='$(ENV)/coverage' '$(ENV)/bin/python' -m coverage report

# Regenerates the locks from requirements/*.in. A release younger than three
# days is not eligible. The age is written as a duration, so the command in
# the lock header, which Renovate replays to recompile a lock, stays valid.
lock:
	cd requirements && for lock in dev semgrep; do \
		uv pip compile --universal --generate-hashes --python-version=3.11 \
			--exclude-newer=P3D \
			$$lock.in --output-file=$$lock.txt || exit 1; \
	done

clean:
	rm -rf .venv dist build src/*.egg-info .mypy_cache .ruff_cache .pytest_cache
