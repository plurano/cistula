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
   (comparison recorded on pull request 2 of the private predecessor repository
   `plurano/python-library`, comment 6104646636).
2. **The repository is private and nothing is released yet**
   (`visibility: private` in [.standards.yaml](../../.standards.yaml);
   `version` in [pyproject.toml](../../pyproject.toml)). The name `cistula`
   is chosen (fleet answer ledger, `python-library-name`, 2026-10-12) and is
   final with the first release. Nothing is published before the repository
   turns public. The first release, version 0.1.0, follows that change and
   goes to PyPI by trusted publishing and to a forge release (fleet answer
   ledger, `python-library-first-release`, 2026-10-12; before it
   `python-library-release-precedent`, 2026-10-11).
3. **The declared supply chain is ahead of the workflows.**
   [.config/archetypes/framework.yaml](../../.config/archetypes/framework.yaml)
   declares `slsa_level: 3`, `enforce_cosign: true` and `require_sbom: true`.
   Until the change that adds the workflows below, `.standards.yaml` carried
   one HISS-11 exception for `.github/workflows/release.yml`. That change
   removes it: with the two workflows, the parts lists and the signing steps
   in place, `praetorctl audit` reports the entry as excusing no gap.
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

While the repository is private, the version stays `0.1.0.dev0` and no tag is
cut, not even a release candidate: a tag stays for good, and the name is final
only with the first release. The first tag comes with the first public
release (fleet answer ledger, `python-library-pilot-pin`, 2026-10-11).

### One build path

The release job builds with the targets of the gate and adds no second way to
build. `make verify` (environment, static checks, `package`,
`verify-reproducible`, tests on every interpreter) is the build step. A
release file is therefore a file the gate has built twice and tested.

### Two workflows

| File | Trigger | Content |
| :--- | :--- | :--- |
| `.github/workflows/release.yml` | Push of a tag `v*`; manual run (`workflow_dispatch`) | `build` calls the reusable workflow with input `sign` (`true` on tag, `false` on dispatch). On tags only: `verify`, `pypi`, and `draft` jobs. Top-level `permissions: contents: read` |
| `.github/workflows/release-build.yml` | `workflow_call` with required boolean input `sign` | One job `build`: the steps listed below. `permissions: contents: read`, `id-token: write`, `attestations: write` |

The split is what the standards engine measures as SLSA Build Level 3: the
build runs in a reusable workflow of this repository, in the job that also
attests it, and nothing is downloaded into that job before the attestation
(engine guide `docs/guides/releasing.md` at the commit that
`PRAETOR_REF` in [ci.yml](../../.github/workflows/ci.yml) pins). It is built
in from the start so that the build path does not change when signing is
added.

PyPI does not accept a reusable workflow as a trusted publisher
([PyPI troubleshooting page](https://docs.pypi.org/trusted-publishers/troubleshooting/)).
The upload is therefore a job of
[release.yml](../../.github/workflows/release.yml) itself, and the publisher
registered on PyPI names that file and the environment `pypi`.

Steps of the one job in `release-build.yml`, in order:

1. Check out with full history and without stored credentials; install the
   supported interpreters as [ci.yml](../../.github/workflows/ci.yml) does.
2. On a tag: refuse unless the tag is `v` plus the `version` of
   [pyproject.toml](../../pyproject.toml), and unless the tagged commit is an
   ancestor of `origin/main`.
3. `make verify REUSE="pipx run reuse==6.2.0"`.
4. Write `SHA256SUMS` for the two files of `dist/` and print it into the run
   summary (`$GITHUB_STEP_SUMMARY`).
5. Write the parts lists of the unpacked wheel with syft, as SPDX JSON
   (`cistula-<v>.spdx.json`) and as CycloneDX JSON
   (`cistula-<v>.cyclonedx.json`), the two formats other fleet repositories
   emit.
6. If `sign` is true: sign `SHA256SUMS` with keyless cosign into
   `SHA256SUMS.sigstore.json`, and attest the build provenance of the two
   files listed in it with the forge's attestation action. If `sign` is false
   (dry run): print one notice that the files are not signed and not
   attested.
7. Hand `dist/` (`dist` files, `SHA256SUMS`, parts lists, and the bundle if
   signed) to the calling workflow as artifact `release-files`.

Every action is pinned by commit, and a release of an action younger than
three days is not eligible, as for the packages of `requirements/dev.txt`.

### Private phase: a dry run

Until the first public release the workflow publishes nothing: no forge
release, no package index, and no signature. A manual dispatch run on `main`
runs the `build` job only, passing `sign: false`. The downstream jobs
(`verify`, `pypi`, `draft`) are guarded to run on tag pushes only
(`if: startsWith(github.ref, 'refs/tags/v')`), so a dispatch run produces only
the unsigned run artifacts and the printed hashes. Step 6 states the missing
signature in the log, so the gap is visible in every dry run and not only in
this record.

### Public phase

The publication channels are decided (fleet answer ledger,
`python-library-first-release`, 2026-10-12): the first release of `cistula`,
version 0.1.0, follows the change of the repository to public. When a tag
`v*` is pushed:

1. `build` invokes [.github/workflows/release-build.yml](../../.github/workflows/release-build.yml)
   with `sign: true`, building the package with `make verify`, writing
   `SHA256SUMS`, generating Syft SBOMs, signing `SHA256SUMS` with Cosign, and
   attesting build provenance.
2. `verify` reads back the artifacts without builder privileges, checking
   `SHA256SUMS`, verifying the Cosign signature and provenance attestations, and
   verifying that planted defects in checksums, files, or signer workflow
   identities are refused.
3. `pypi` uploads only the built wheel and source distribution to PyPI via
   trusted publishing under the `pypi` environment, using
   `pypa/gh-action-pypi-publish`.
4. `draft` creates a draft release on the forge with all release files
   (distributions, `SHA256SUMS`, Cosign signature bundle, and SBOMs) for manual
   review and publication by a maintainer.

A consumer verifies the released files with these two checks:

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

- Whether a dry run on `main` and a tag run produce the files and the
  notices described here; the first dry run after merging settles the build
  half while the repository is private.
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
  reproducible build are its only proof of origin. The files are stored
  uncompressed and are about five times and two and a half times as large as
  compressed ones (README, "No compression"). Run artifacts expire with the
  forge's retention time, so a pinned build is recovered by building the
  commit again, not from storage.
