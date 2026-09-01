# upgrade-impact-triage

Analysis payload for a Rote Play that answers the question dependency tooling skips:

> Of my outdated dependencies, which ones have breaking changes that touch code I actually call?

Not "is there a CVE" — that is the published `modiqo/dependency-vulnerability-check`. Not "is there
a newer version" — that is Dependabot. The gap in between.

## The ranking, which is the whole point

| Tier | Meaning |
|---|---|
| `ACT` | Breaking changes **and** you import it directly — your problem today |
| `REVIEW` | You import it directly, but breaking status is **unknown** because the notes could not be read |
| `SAFE` | Outdated but never imported directly, or the notes were read and were clean |
| `CURRENT` | Already at the latest version |

**`REVIEW` is load-bearing.** An unreadable changelog must never be laundered into "safe" — that
would tell someone an upgrade is fine when nobody checked. A rate-limited run produces more
`REVIEW` rows; it never produces fewer `ACT` rows. Verified exhaustively over all eight
`direct` × `checked` × `breaking` combinations.

## Step contract

Each script is one rote step, following the runtime's two-lane failure model:

| Situation | Behaviour |
|---|---|
| Expected absence (unknown package, flaky registry, no changelog) | `{"ok": true, "warning": ...}` on stdout, **exit 0** — a labeled degraded row |
| Hard fault (bad invocation, unreadable root) | message on **stderr**, **exit 2** — dependents BLOCKED, `--resume` offered |

stdout is data, exit status is the failure signal. Collections cross step boundaries as a
delimited scalar (`chr(31)` fields, `chr(30)` records) because value-edge jq must resolve to a
scalar. Standard library only, so `deps.toml` declares `python3` and nothing else.

## Steps

| Script | Role |
|---|---|
| `parse_manifest.py <root>` | Walk a project; parse `package.json` / `pyproject.toml` / `requirements.txt` / `Cargo.toml` into one flat deduplicated dependency list |
| `fetch_registry.py <eco> <name> [current]` | One dependency, one registry reading: latest version, major/minor/patch gap, GitHub source repo |
| `fetch_changelog.py <owner/repo> <cur> <latest>` | Read release notes in the version range and judge them breaking |
| `find_callsites.py <root> <eco> <name>` | Is it imported directly, and where — the step that separates this from Dependabot |
| `compute_verdict.py` | Join everything from stdin JSONL into the ranked verdict |

`smoke_test.sh` runs the whole chain locally. **It is not the exploration path** — running one
script that does everything would make rote record a single opaque step with no edges. During the
recorded exploration, each reading gets its own `rote proc run` capture so independent readings
become parallel root steps.

## Optional GITHUB_TOKEN

The GitHub REST API allows 60 unauthenticated requests per hour, and a 47-dependency project
exhausts that. `GITHUB_TOKEN` raises it to 5000/hr. It is deliberately **optional**: the Play runs
with no credentials at all and simply reports more `REVIEW` rows without one, which keeps the
zero-setup adoption story intact.

## Verified

Live reads against npm, PyPI and crates.io. Correct gap classification (express 4.17.1 → 5.2.1
major, requests minor, serde patch, left-pad none). Call sites verified against ground truth in
this repository. Changelog parsing unit-tested offline against fixture release bodies. Negative
space covered: unknown package, unsupported ecosystem, empty directory, malformed manifest, empty
stdin, bad invocation.

The GitHub API is unreachable from the development container, so `fetch_changelog.py`'s success
path is **unverified against the live API** — its parsing, version-range filtering and every
degrade path are tested; the happy path needs one run on a machine with GitHub access.

### Bugs found and fixed while testing

- **crates.io answers 403 without a `User-Agent` header** — would have silently broken the whole
  Rust path.
- **PyPI `project_urls` keys are author-supplied and vary in case** — numpy publishes `source`,
  others `Source Code`. Exact-case lookup dropped the repo for numpy and packages like it.
- **Repo normalisation took the last two path segments**, so the tracker URL
  `github.com/numpy/numpy/issues` resolved to the non-existent repo `numpy/issues`. Now anchors on
  the first two segments after the host.
- **A malformed manifest reported "no manifest found"**, discarding the parse error. A degraded
  source has to be a visible unknown, so it now names the file and the failure.
- **`^\s*` under `re.M` matched across newlines**, so Python import line numbers pointed at blank
  lines. Now `^[ \t]*`. Call-site line numbers are the Play's core promise; wrong ones destroy it.
- **Commented-out `require()` counted as a live call site**, inflating the very count this Play
  exists to shrink. Whole-line comments are now skipped — a live import in the same file still
  registers.
- **`re.split` used positional `maxsplit`**, deprecated in Python 3.13+ and liable to emit stderr
  noise inside a step.
