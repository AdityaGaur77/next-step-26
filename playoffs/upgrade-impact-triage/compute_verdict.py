#!/usr/bin/env python3
"""Step 5 — join registry, changelog and call-site facts into a ranked verdict.

Reads one JSON object per line on stdin, each merging what the earlier steps
learned about a single dependency. Emits the canonical result.

The ranking rule, and the reason the Play is worth running:

  ACT      breaking changes AND you import it directly.       -> your problem, today
  REVIEW   you import it directly, but breaking status is
           UNKNOWN because notes could not be read.           -> nobody checked; you decide
  SAFE     outdated, but you never import it directly, or
           the notes were read and were clean.                -> bump it blind
  CURRENT  already at the latest version.

REVIEW exists so an unreadable changelog can never be laundered into "safe".
A rate-limited run reports more REVIEW rows; it never reports fewer ACT rows.
"""
import json
import sys

FS = chr(31)
RS = chr(30)

ORDER = {"ACT": 0, "REVIEW": 1, "SAFE": 2, "CURRENT": 3}
GAP_WEIGHT = {"major": 0, "minor": 1, "patch": 2, "none": 3, "unknown": 4, "ahead": 5}


def die(msg):
    print(f"compute_verdict: {msg}", file=sys.stderr)
    raise SystemExit(2)


def classify(rec):
    if not rec.get("outdated"):
        return "CURRENT", "already current"
    direct = bool(rec.get("direct"))
    checked = bool(rec.get("checked"))
    breaking = bool(rec.get("breaking"))

    if not direct:
        return "SAFE", "not imported directly; transitive"
    if breaking:
        return "ACT", rec.get("markers") or "breaking changes in range"
    if not checked:
        return "REVIEW", rec.get("warning") or "release notes unavailable; status unknown"
    return "SAFE", "notes read, no breaking markers"


def main():
    records = []
    for line_no, line in enumerate(sys.stdin, 1):
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            die(f"line {line_no} is not valid JSON: {exc}")

    if not records:
        sys.stdout.write(json.dumps({
            "ok": True, "warning": "no dependency records on stdin",
            "total": 0, "act": 0, "review": 0, "safe": 0, "current": 0,
            "headline": "nothing to triage", "packed": "",
        }) + "\n")
        raise SystemExit(0)

    rows = []
    for rec in records:
        tier, why = classify(rec)
        rows.append({
            "tier": tier,
            "ecosystem": rec.get("ecosystem", "?"),
            "name": rec.get("name", "?"),
            "current": rec.get("current", ""),
            "latest": rec.get("latest", ""),
            "gap": rec.get("gap", "unknown"),
            "direct": bool(rec.get("direct")),
            "files": rec.get("files", 0),
            "why": why,
            "site": rec.get("first_site", ""),
        })

    rows.sort(key=lambda r: (ORDER[r["tier"]], GAP_WEIGHT.get(r["gap"], 9), r["name"]))
    counts = {t: sum(1 for r in rows if r["tier"] == t) for t in ORDER}
    outdated = counts["ACT"] + counts["REVIEW"] + counts["SAFE"]

    if counts["ACT"]:
        headline = (f"{counts['ACT']} of {len(rows)} dependencies have breaking changes "
                    f"in code you actually call")
    elif counts["REVIEW"]:
        headline = (f"no confirmed breaking changes, but {counts['REVIEW']} "
                    f"could not be verified")
    elif outdated:
        headline = f"{outdated} outdated, none of them breaking for your code"
    else:
        headline = "everything current"

    packed = RS.join(FS.join([
        r["tier"], r["ecosystem"], r["name"], r["current"] or "-", r["latest"] or "-",
        r["gap"], str(r["files"]), r["why"], r["site"],
    ]) for r in rows)

    sys.stdout.write(json.dumps({
        "ok": True,
        "total": len(rows),
        "act": counts["ACT"], "review": counts["REVIEW"],
        "safe": counts["SAFE"], "current": counts["CURRENT"],
        "outdated": outdated,
        "headline": headline,
        "packed": packed,
    }) + "\n")


if __name__ == "__main__":
    main()
