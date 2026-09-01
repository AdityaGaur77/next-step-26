#!/usr/bin/env bash
# LOCAL VERIFICATION ONLY — do NOT run this during the recorded exploration.
#
# rote crystallizes the DAG from what it observes. If it sees this one script,
# it records ONE opaque step with no edges: nothing to parallelize, checkpoint,
# resume, or blame per source. During exploration, run each reading as its own
# `rote proc run` capture so independent readings become parallel root steps.
#
# This exists to prove the pieces work together before that recording starts.
set -uo pipefail
ROOT="${1:-.}"
HERE="$(cd "$(dirname "$0")" && pwd)"

manifest=$(python3 "$HERE/parse_manifest.py" "$ROOT") || exit 2
count=$(printf '%s' "$manifest" | python3 -c 'import json,sys;print(json.load(sys.stdin)["count"])')
echo >&2 "manifests: $(printf '%s' "$manifest" | python3 -c 'import json,sys;print(json.load(sys.stdin)["manifests"] or "-")')"
echo >&2 "dependencies: $count"
[ "$count" -eq 0 ] && { echo >&2 "nothing to triage"; exit 0; }

printf '%s' "$manifest" \
  | python3 -c 'import json,sys
d=json.load(sys.stdin)
for rec in d["packed"].split(chr(30)):
    print("\t".join(rec.split(chr(31))))' \
  | while IFS=$'\t' read -r eco name cur; do
      reg=$(python3 "$HERE/fetch_registry.py" "$eco" "$name" "$cur")
      repo=$(printf '%s' "$reg" | python3 -c 'import json,sys;print(json.load(sys.stdin)["repo"])')
      latest=$(printf '%s' "$reg" | python3 -c 'import json,sys;print(json.load(sys.stdin)["latest"])')
      sites=$(python3 "$HERE/find_callsites.py" "$ROOT" "$eco" "$name")
      chg=$(python3 "$HERE/fetch_changelog.py" "$repo" "$cur" "$latest")
      python3 -c '
import json,sys
reg,sites,chg = (json.loads(a) for a in sys.argv[1:4])
first = sites["packed"].split(chr(30))[0].split(chr(31)) if sites["packed"] else None
out = dict(reg)
out.update({k: sites.get(k) for k in ("direct","files")})
out.update({k: chg.get(k) for k in ("checked","breaking","markers")})
out["first_site"] = f"{first[0]}:{first[1]}" if first else ""
if chg.get("warning"): out["warning"] = chg["warning"]
print(json.dumps(out))' "$reg" "$sites" "$chg"
    done \
  | python3 "$HERE/compute_verdict.py"
