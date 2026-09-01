#!/usr/bin/env python3
"""Step 1 — find dependency manifests under a root and emit one flat dependency list.

Contract (rote step):
  stdout is data (one JSON object), exit status is the failure signal.
  Expected absence -> {"ok": true, "warning": ...} exit 0.
  Hard fault       -> message on stderr, exit 2.

Collections cross step boundaries as a delimited scalar, because value-edge jq
must resolve to a scalar. Records are RS-separated, fields FS-separated:
  ecosystem FS name FS current_spec
"""
import json
import os
import re
import sys

FS = chr(31)
RS = chr(30)

SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", "target",
    "dist", "build", ".tox", ".mypy_cache", "site-packages", ".next",
}


def die(msg):
    print(f"parse_manifest: {msg}", file=sys.stderr)
    raise SystemExit(2)


def emit(payload):
    sys.stdout.write(json.dumps(payload) + "\n")
    raise SystemExit(0)


def clean_version(spec):
    """Strip range operators to a bare version. '^1.2.3' -> '1.2.3'."""
    if not isinstance(spec, str):
        return ""
    m = re.search(r"(\d+(?:\.\d+)*(?:[-+][0-9A-Za-z.\-]+)?)", spec)
    return m.group(1) if m else ""


def from_package_json(path):
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for field in ("dependencies", "devDependencies"):
        for name, spec in (data.get(field) or {}).items():
            # Skip non-registry specs: file:, link:, git+, workspace:, npm alias
            if isinstance(spec, str) and re.match(r"^(file:|link:|git|https?:|workspace:|npm:)", spec):
                continue
            out.append(("npm", name, clean_version(spec)))
    return out


def from_pyproject(path):
    try:
        import tomllib
    except ModuleNotFoundError:
        return []
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out = []
    project = data.get("project") or {}
    for entry in project.get("dependencies") or []:
        name = re.split(r"[<>=!~\[; ]", entry.strip(), maxsplit=1)[0]
        if name:
            out.append(("pypi", name, clean_version(entry)))
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, dict):
            spec = spec.get("version", "")
        out.append(("pypi", name, clean_version(spec)))
    return out


def from_requirements(path):
    out = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.split("#", 1)[0].strip()
            if not line or line.startswith("-"):
                continue
            name = re.split(r"[<>=!~\[; ]", line, maxsplit=1)[0]
            if name:
                out.append(("pypi", name, clean_version(line)))
    return out


def from_cargo(path):
    try:
        import tomllib
    except ModuleNotFoundError:
        return []
    with open(path, "rb") as fh:
        data = tomllib.load(fh)
    out = []
    for field in ("dependencies", "dev-dependencies"):
        for name, spec in (data.get(field) or {}).items():
            if isinstance(spec, dict):
                if "path" in spec or "git" in spec:
                    continue
                spec = spec.get("version", "")
            out.append(("crates", name, clean_version(spec)))
    return out


READERS = {
    "package.json": from_package_json,
    "pyproject.toml": from_pyproject,
    "requirements.txt": from_requirements,
    "Cargo.toml": from_cargo,
}


def main():
    root = sys.argv[1] if len(sys.argv) > 1 else "."
    if not os.path.isdir(root):
        die(f"root is not a directory: {root}")

    deps, seen, manifests, unreadable = [], set(), [], []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        for fname in filenames:
            reader = READERS.get(fname)
            if not reader:
                continue
            full = os.path.join(dirpath, fname)
            try:
                found = reader(full)
            except Exception as exc:                      # noqa: BLE001
                unreadable.append(f"{os.path.relpath(full, root)}: {exc}")
                continue
            manifests.append(os.path.relpath(full, root))
            for eco, name, ver in found:
                key = (eco, name.lower())
                if key in seen:
                    continue
                seen.add(key)
                deps.append((eco, name, ver))

    if not manifests:
        if unreadable:
            warning = "every manifest found failed to parse: " + "; ".join(unreadable)
        else:
            warning = f"no supported manifest found under {root}"
        emit({
            "ok": True, "warning": warning,
            "count": 0, "packed": "", "manifests": "", "ecosystems": "",
        })

    packed = RS.join(FS.join([e, n, v]) for e, n, v in deps)
    payload = {
        "ok": True,
        "count": len(deps),
        "packed": packed,
        "manifests": ",".join(sorted(manifests)),
        "ecosystems": ",".join(sorted({e for e, _, _ in deps})),
    }
    if unreadable:
        payload["warning"] = "; ".join(unreadable)
    emit(payload)


if __name__ == "__main__":
    main()
