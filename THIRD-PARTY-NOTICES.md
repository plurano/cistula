<!--
SPDX-FileCopyrightText: 2026 Lusoris <lusoris@proton.me>
SPDX-License-Identifier: EUPL-1.2
-->

# Third-party notices

Cistula is licensed under the EUPL-1.2 ([`LICENSE`](LICENSE)). The package uses the Python
standard library only, so the wheel and the source archive contain no code that others wrote.

The repository also holds three agent skills that the Praetor engine writes during adoption,
under `.agents/skills/` and, for one coding client, `.claude/skills/`. The skill texts are
the engine's own and carry its EUPL-1.2 header. Each skill is adapted from an MIT-licensed
upstream, and the upstream licence text, with the upstream holder and the full MIT terms, sits
beside it as `LICENSE`, copied byte for byte. The REUSE specification skips files named
`LICENSE`, so [`REUSE.toml`](REUSE.toml) carries no entry for them; this file and the table
below are the record of their terms.

| Skill | Upstream | Licence | Copyright |
| --- | --- | --- | --- |
| `caveman` | [JuliusBrussee/caveman](https://github.com/JuliusBrussee/caveman), commit `8dffbb260` | MIT | 2026 Julius Brussee |
| `adhd-format` | [ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd), commit `d1671755d` | MIT | 2026 Ayoub Ghriss |
| `social-text` | [ayghri/i-have-adhd](https://github.com/ayghri/i-have-adhd), through `adhd-format` | MIT | 2026 Ayoub Ghriss |

The upstream commits are the ones the engine's credits name (`docs/credits.yaml` in
[cordanaLLM/praetor](https://github.com/cordanaLLM/praetor)). The caveman upstream moved to
Apache-2.0 in its release 3.0.0; the skill was adapted before that change, under the MIT
terms shipped here. The caveman licence text opens with the upstream's scope note: it puts a
set of runtime directories of that repository under the Business Source License 1.1. The
upstream's `LICENSING.md` at that commit lists its `skills/` directory, where the skill came
from, as MIT. The adapted skill copies no upstream text.
