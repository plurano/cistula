# ADR-0001: Release job: one reproducible build, a dry run while the repository is private

- **Status**: Proposed
- **Date**: 2026-10-11
- **Authors**: lusoris

## Context

Milestone 0.1.0 ends with a package that two pilot repositories install and
pin by hash. Four facts shape the job that builds it.

1. **The bytes depend on the commit only.** `make package` gives the same
   source distribution and the same wheel on every host
   ([README](../../README.md#reproducible-package)), and
   `make verify-reproducible` fails the gate on one differing byte
   ([Makefile](../../Makefile), [tools/compare_packages.py](../../tools/compare_packages.py),
   [tests/test_compare_packages.py](../../tests/test_compare_packages.py)).
   A hash therefore names a commit, not a build host or a build run.
   Measured across hosts for commit `02ae449`: the run on `main` and a local
   build on another distribution and interpreter patch version print the same
   two hashes
   ([comparison](https://github.com/plurano/python-library/pull/2#issuecomment-6104646636)).
2. **The repository is private and its name is a working name**
   (`visibility: private` and the description in
   [.standards.yaml](../../.standards.yaml); `name` and `version` in
   [pyproject.toml](../../pyproject.toml)). Nothing is published before the
   first public release, and that release, its name and its place of
   publication are the operator's decision (fleet answer ledger,
   `python-library-release-precedent`, 2026-10-11).
3. **The declared supply chain is ahead of the workflows.**
   [.config/archetypes/framework.yaml](../../.config/archetypes/framework.yaml)
   declares `slsa_level: 3`, `enforce_cosign: true` and `require_sbom: true`.
   No release workflow exists yet, so `.standards.yaml` carries one HISS-11
   exception for `.github/workflows/release.yml` that expires on 2027-01-09.
4. **Signing a private build has no place to go yet.** The forge's build
   attestations are not offered to private repositories on the current plan,
   and a keyless cosign signature writes the repository and workflow identity
   into a public transparency log. Signing with an own key waits for the
   fleet's secret store (same ledger entry). The plan limit is taken from that
   entry; the two forge documentation pages read for this record
   (`actions/concepts/security/artifact-attestations`,
   `actions/how-tos/secure-your-work/use-artifact-attestations`) do not state
   it.

No other fleet repository has a release workflow for a Python package to
reuse; this job is the first input to that shared convention (same ledger
entry).

## Decision

### What a pilot pins

A pilot pins **a commit on `main` and the SHA-256 of the wheel built from
it**. Any clean checkout of that commit gives the file:

```sh
git switch --detach <commit>
make package          # prints the two hashes of dist/
```

The hash to pin is the one the `Verification gate` run on `main` prints for
that commit ([.github/workflows/ci.yml](../../.github/workflows/ci.yml)); a
local build must print the same one. The hashes a pull request run prints are
not to be pinned: that run builds the forge's test merge commit, which has its
own date (README, "Two limits").

While the repository is private and carries a working name, the version stays
`0.1.0.dev0` and no tag is cut, not even a release candidate: a tag stays for
good, and the name is not final. The first tag comes with the first public
release (fleet answer ledger, `python-library-pilot-pin`, 2026-10-11).

### One build path

The release job builds with the targets of the gate and adds no second way to
build. `make verify` (environment, static checks, `package`,
`verify-reproducible`, tests on every interpreter) is the build step. A
release file is therefore a file the gate has built twice and tested.

### Two workflows

| File | Trigger | Content |
| :--- | :--- | :--- |
| `.github/workflows/release.yml` | manual run on `main`; from the first public release also the push of a tag `v*` | One job that calls the workflow below. `permissions: contents: read` |
| `.github/workflows/release-build.yml` | `workflow_call` only | One job: the steps listed below |

The split is what the standards engine measures as SLSA Build Level 3: the
build runs in a reusable workflow of this repository, in the job that also
attests it, and nothing is downloaded into that job before the attestation
(engine guide `docs/guides/releasing.md` at the commit that
`PRAETOR_COMMIT` in [ci.yml](../../.github/workflows/ci.yml) pins). It is built in from the start
so that the build path does not change when signing is added.

Steps of the one job in `release-build.yml`, in order:

1. Check out with full history and without stored credentials; install the
   three interpreters as `ci.yml` does.
2. On a tag: refuse unless the tag is `v` plus the `version` of
   `pyproject.toml`, and unless the tagged commit is an ancestor of
   `origin/main`.
3. `make verify`.
4. Write `SHA256SUMS` for the two files of `dist/` and print it into the run
   summary.
5. Write the parts lists of the two files with the syft action, as SPDX JSON
   and as CycloneDX JSON (the two formats other fleet repositories emit).
6. Private phase: print one line that says the files are **not signed and not
   attested**, and why. Public phase: attest the build provenance of the two
   files with the forge's attestation action, then sign `SHA256SUMS` with
   keyless cosign into `SHA256SUMS.sigstore.json`.
7. Keep `dist/`, `SHA256SUMS`, the parts lists and, in the public phase, the
   bundle as artifacts of the run.

Every action is pinned by commit, and a release of an action younger than
three days is not eligible, as for the packages of `requirements/dev.txt`.

### Private phase: a dry run

Until the first public release the job publishes nothing: no forge release,
no package index, no signature. Its result is the run's artifacts and the
printed hashes. Step 6 states the missing signature in the log, so the gap is
visible in every run and not only in this record.

The HISS-11 exception stays for the private phase, with its reason narrowed to
what is still missing (attestation and cosign signature), and is removed with
the change that adds them. `praetorctl audit` decides whether a gap is left;
an exception that names no gap fails the audit as stale.

### Public phase

Decided with the first public release, not here: the final name, the version
tag, forge release assets and a package index. Fixed here is only the shape:
the same job, with step 6 signing, a draft forge release that is published as
the last step after the bundle and the attestation verify, and these two
checks for a consumer:

```sh
gh attestation verify <file> -R <owner>/<repository>
cosign verify-blob \
  --certificate-identity "https://github.com/<owner>/<repository>/.github/workflows/release-build.yml@refs/tags/<tag>" \
  --certificate-oidc-issuer https://token.actions.githubusercontent.com \
  --bundle SHA256SUMS.sigstore.json SHA256SUMS
```

### Not yet measured

Each point is settled by a run before this record is accepted, or with the
change that adds the step.

- Whether `praetorctl audit` credits the parts-list step and the two-workflow
  shape while the signing steps are absent.
- The certificate identity a signature made inside the called workflow
  carries; the command above assumes the called workflow's path at the tag.
  The check ships with a wrong identity that it refuses.
- Whether the parts lists are the same bytes on every run. They are not part
  of the pinned bytes either way.
- The plan limit of the forge's attestations for a private repository
  (Context, point 4).

## Consequences

- **Positive**: A pilot can check its pinned hash against a build of its own;
  no trust in a build host is needed, and no token or copied source tree. The
  release file is built by the gate's targets, so there is one build to keep
  correct. The step from the private to the public phase adds steps and
  changes none.
- **Negative**: A private-phase build carries no signature; the hash and the
  reproducible build are its only proof of origin. The repository keeps a
  HISS-11 exception until the public phase. The files are stored
  uncompressed and are about five times and two and a half times as large as
  compressed ones (README, "No compression"). Run artifacts expire with the
  forge's retention time, so a pinned build is recovered by building the
  commit again, not from storage.
