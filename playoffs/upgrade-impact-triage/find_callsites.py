#!/usr/bin/env python3
"""Step 4 — does this project actually import the package, and where?

This is the step that separates the Play from Dependabot. A breaking change in
a dependency you never import directly is not your problem; the same change in
one you call on line 42 is.

Contract (rote step):
  Never a hard fault for "not found" — a package with no call sites is a REAL
  ANSWER, not an absence. Only a bad invocation or unreadable root exits 2.

Standard library only. Walks the tree once rather than shelling out, so no
ripgrep dependency lands in deps.toml.
"""
import json
import os
import re
import sys

FS = chr(31)
RS = chr(30)

MAX_BYTES = 1_500_000        # skip anything bigger; it is not hand-written source
MAX_HITS = 40

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "target", "dist",
    "build", ".tox", ".mypy_cache", "site-packages", ".next", "vendor",
    "coverage", ".pytest_cache", ".ruff_cache", "htmlcov",
}

EXTS = {
    "npm":    {".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".svelte", ".vue"},
    "pypi":   {".py", ".pyi"},
    "crates": {".rs"},
}


def die(msg):
    print(f"find_callsites: {msg}", file=sys.stderr)
    raise SystemExit(2)


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    raise SystemExit(0)


def import_aliases(eco, name):
    """Plausible module names for a distribution name.

    Distribution name and import name often differ (PyPI beautifulsoup4 imports
    as bs4). We cannot resolve that from the registry alone, so we try the
    mechanical transforms and report honestly when nothing matches.
    """
    base = name.strip()
    out = {base}
    if eco == "pypi":
        out.add(base.replace("-", "_"))
        out.add(base.replace("-", ""))
        out.add(base.replace("_", "-"))
        if base.lower().startswith("python-"):
            out.add(base[7:].replace("-", "_"))
    elif eco == "crates":
        out.add(base.replace("-", "_"))
    elif eco == "npm":
        out.add(base)                       # scoped names like @scope/pkg stay whole
    return {a for a in out if a}


def patterns_for(eco, aliases):
    pats = []
    for alias in aliases:
        q = re.escape(alias)
        if eco == "npm":
            pats.append(re.compile(
                rf"""(?:require\s*\(\s*['"]{q}(?:/[^'"]*)?['"]\s*\)"""
                rf"""|from\s+['"]{q}(?:/[^'"]*)?['"]"""
                rf"""|import\s*\(\s*['"]{q}(?:/[^'"]*)?['"]\s*\))"""))
        elif eco == "pypi":
            # [ \t]* not \s* — under re.M, \s matches newlines, so the match would
            # start on an earlier blank line and report the wrong line number.
            pats.append(re.compile(
                rf"^[ \t]*(?:from\s+{q}(?:\.\w+)*\s+import\s+|import\s+{q}(?:\.\w+)*)",
                re.M))
        elif eco == "crates":
            pats.append(re.compile(
                rf"(?:^[ \t]*use\s+{q}\s*(?:::|;)|^[ \t]*extern\s+crate\s+{q}\b)", re.M))
    return pats


COMMENT_PREFIXES = ("//", "*", "/*", "#")


def is_commented(line):
    """True when the matched line is a whole-line comment.

    The Python and Rust patterns anchor with ^[ \\t]* so a leading # or // already
    prevents a match. The npm pattern cannot anchor — require() legitimately
    appears mid-line — so a commented-out require would otherwise be reported as
    a live call site and inflate the count this Play exists to shrink.
    """
    return line.lstrip().startswith(COMMENT_PREFIXES)


def main():
    if len(sys.argv) < 4:
        die("usage: find_callsites.py <root> <ecosystem> <name>")
    root, eco, name = sys.argv[1], sys.argv[2], sys.argv[3]

    if not os.path.isdir(root):
        die(f"root is not a directory: {root}")

    exts = EXTS.get(eco)
    base = {"ok": True, "ecosystem": eco, "name": name,
            "direct": False, "hits": 0, "files": 0, "packed": "", "scanned": 0}
    if not exts:
        base["warning"] = f"no source pattern for ecosystem: {eco}"
        emit(base)

    aliases = import_aliases(eco, name)
    pats = patterns_for(eco, aliases)

    hits, files_with, scanned, unreadable = [], set(), 0, 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            if os.path.splitext(fname)[1] not in exts:
                continue
            full = os.path.join(dirpath, fname)
            try:
                if os.path.getsize(full) > MAX_BYTES:
                    continue
                with open(full, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
            except OSError:
                unreadable += 1
                continue
            scanned += 1
            lines = text.splitlines()
            for pat in pats:
                for m in pat.finditer(text):
                    line_no = text.count("\n", 0, m.start()) + 1
                    raw = lines[line_no - 1] if line_no <= len(lines) else ""
                    if is_commented(raw):
                        continue              # a commented-out import is not a call site
                    rel = os.path.relpath(full, root)
                    files_with.add(rel)
                    if len(hits) < MAX_HITS:
                        hits.append((rel, str(line_no), raw.strip()[:160]))
                    break                     # one live hit per pattern per file is enough

    base.update({
        "direct": bool(files_with),
        "hits": len(hits),
        "files": len(files_with),
        "scanned": scanned,
        "packed": RS.join(FS.join(h) for h in hits),
    })
    if not files_with:
        base["note"] = (f"{name} is not imported directly in {scanned} scanned "
                        f"source files — likely transitive")
    if unreadable:
        base["warning"] = f"{unreadable} file(s) could not be read"
    emit(base)


if __name__ == "__main__":
    main()
