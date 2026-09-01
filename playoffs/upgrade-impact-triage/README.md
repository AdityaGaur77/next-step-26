# upgrade-impact-triage

Analysis payload for a Rote Play that answers the question dependency tooling skips:

> Of my outdated dependencies, which ones have breaking changes that touch code I actually call?

Not "is there a CVE" (that is `modiqo/dependency-vulnerability-check`) and not "is there a newer
version" (that is Dependabot). The gap in between.

## Step contract

Each script is one rote step. They follow the runtime's two-lane failure model:

| Situation | Behaviour |
|---|---|
| Expected absence (package unknown, registry flaky, no changelog) | `{"ok": true, "warning": ...}` on stdout, **exit 0** — a labeled degraded row |
| Hard fault (bad invocation, unreadable root) | message on **stderr**, **exit 2** — dependents BLOCKED, `--resume` offered |

stdout is data, exit status is the failure signal. Collections cross step boundaries as a
delimited scalar (`chr(31)` fields, `chr(30)` records) because value-edge jq must resolve to a
scalar.

Standard library only, so `deps.toml` declares `python3` and nothing else.

## Steps

| Script | Role |
|---|---|
| `parse_manifest.py <root>` | Walk a project, parse `package.json` / `pyproject.toml` / `requirements.txt` / `Cargo.toml`, emit one flat deduplicated dependency list |
| `fetch_registry.py <eco> <name> [current]` | One dependency, one registry reading: latest version, major/minor/patch gap, and GitHub source repo |

## Verified against live registries

npm, PyPI and crates.io reads all confirmed working. Two findings worth keeping:

- **crates.io returns 403 without a `User-Agent` header.** Every request sets one.
- **PyPI `project_urls` keys are author-supplied and vary in case** — numpy publishes `source`,
  others publish `Source Code`. Lookup is case-insensitive with a priority list, then falls back
  to scanning every value for a GitHub URL.
- Repo normalisation anchors on the **first** two path segments after the host, so a tracker URL
  such as `github.com/numpy/numpy/issues` resolves to `numpy/numpy`, never `numpy/issues`.
