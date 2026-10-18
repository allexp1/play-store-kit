#!/usr/bin/env python3
"""Validate a play-store-kit folder: schemas of every kit file and a secrets scan (PLAN-FINAL §7, §8.9).

  python3 scripts/validate_kit.py KIT [--only schemas|secrets] [--json]
  python3 scripts/validate_kit.py KIT --init            # copy templates/kit into KIT (never overwrites)

Errors exit 1, warnings alone exit 0. Nothing here touches the network or the Play account.

Secrets: fails when a keystore, keystore.properties, a service-account key or the kit's private/ folder is
tracked by git (a .gitignore entry does not untrack a file), when the service-account key path points inside the
repository, and when a generated file (STATUS.md, CONSOLE-GUIDE.md, PRECHECK.md, play-plan.*, console-live.json)
contains key material, a bearer token or a resumable upload URL. `scan_text()` is the same check for writers.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys

import build

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATES = ROOT / "templates" / "kit"

PACKAGE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9_]*(\.[a-zA-Z][a-zA-Z0-9_]*)+$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{2}(:?[0-9a-fA-F]{2}){31}$")
LOCALE_RE = re.compile(r"^[a-z]{2,3}(-[A-Z]{2}|-[0-9]{3}|-[A-Za-z]{4})?$")
DEVICES = {"phone", "tablet", "wear", "tv", "auto", "automotive", "xr", "chromebook"}
DECLARATIONS = {"ads", "advertising_id", "account_creation", "digital_goods", "genai", "children_audience",
                "financial", "health", "government", "vpn", "ugc"}
KINDS = {"gradle", "flutter", "react-native", "expo", "unity", "external"}
PUBLISHING = {"standard", "managed", "unknown"}
GENERATED = ("STATUS.md", "CONSOLE-GUIDE.md", "PRECHECK.md", "play-plan.md", "play-plan.json", "console-live.json")
TRACKED_FORBIDDEN = ("*.jks", "*.keystore", "keystore.properties", "*service-account*.json", "*-sa.json",
                     "*/private/*", "private/*", "*/captures/*")
SECRET_PATTERNS = [
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "private key material"),
    (re.compile(r'"private_key"\s*:'), "service-account private_key field"),
    (re.compile(r"\bya29\.[0-9A-Za-z_\-]{10,}"), "OAuth access token"),
    (re.compile(r"(?i)\bauthorization:\s*bearer\s+\S{10,}"), "bearer token"),
    (re.compile(r"[?&]upload_id=[0-9A-Za-z_\-]{6,}"), "resumable upload URL"),
    (re.compile(r"(?i)\b(store|key)Password\s*[=:]\s*\S+"), "keystore password"),
]


# ------------------------------------------------------------------------------------------------ findings
class Report:
    def __init__(self):
        self.items: list[dict] = []

    def error(self, where, message):
        self.items.append({"level": "error", "where": where, "message": message})

    def warn(self, where, message):
        self.items.append({"level": "warning", "where": where, "message": message})

    @property
    def errors(self):
        return [i for i in self.items if i["level"] == "error"]


def scan_text(text: str) -> list[str]:
    """Names of the secret kinds found in text (empty when clean). Writers call this before writing."""
    return [name for rx, name in SECRET_PATTERNS if rx.search(text or "")]


def assert_clean(text: str, where: str):
    found = scan_text(text)
    if found:
        raise ValueError(f"refusing to write {where}: it contains {', '.join(found)}")


# ------------------------------------------------------------------------------------------------ helpers
def _load(kit, name, rep):
    p = kit / name
    if not p.exists():
        return None
    try:
        doc = build.load_yaml(p)
    except Exception as e:  # noqa: BLE001 - any parse error is a finding
        rep.error(name, f"not valid YAML: {e}")
        return None
    if doc is not None and not isinstance(doc, dict):
        rep.error(name, "must be a mapping at the top level")
        return None
    return doc or {}


def _is_date(v):
    if isinstance(v, dt.date):
        return True
    try:
        dt.date.fromisoformat(str(v))
        return True
    except ValueError:
        return False


def _url(v):
    return isinstance(v, str) and v.startswith("https://") and len(v) > 10


def _list_of(rep, where, v, name):
    if v is None:
        return []
    if not isinstance(v, list):
        rep.error(where, f"{name} must be a list")
        return []
    return v


# ------------------------------------------------------------------------------------------------ schemas
def check_facts(kit, rep):
    f = _load(kit, "facts.yaml", rep)
    if f is None:
        rep.error("facts.yaml", "missing (python3 scripts/validate_kit.py KIT --init)")
        return {}
    pkg = f.get("package")
    if not (isinstance(pkg, str) and PACKAGE_RE.match(pkg)):
        rep.error("facts.yaml", f"package {pkg!r} is not a valid application id (e.g. com.example.app)")
    elif pkg.startswith(("com.example.", "com.android.", "android.")):
        rep.warn("facts.yaml", f"package {pkg} is a placeholder or reserved prefix; Play refuses it")
    if f.get("category_type") not in (None, "app", "game"):
        rep.error("facts.yaml", "category_type must be app or game")
    if f.get("free_or_paid") not in (None, "free", "paid"):
        rep.error("facts.yaml", "free_or_paid must be free or paid")
    for loc in _list_of(rep, "facts.yaml", f.get("locales"), "locales") + [f.get("default_locale")]:
        if loc is not None and not (isinstance(loc, str) and LOCALE_RE.match(loc)):
            rep.error("facts.yaml", f"locale {loc!r} is not a BCP-47 tag like en-US")
    acct = f.get("account") or {}
    if acct.get("type") not in (None, "personal", "organization"):
        rep.error("facts.yaml", "account.type must be personal or organization")
    if acct.get("created") is not None and not _is_date(acct["created"]):
        rep.error("facts.yaml", "account.created must be YYYY-MM-DD")
    decl = f.get("declarations") or {}
    if not isinstance(decl, dict):
        rep.error("facts.yaml", "declarations must be a mapping")
        decl = {}
    for k, v in decl.items():
        if k not in DECLARATIONS:
            rep.warn("facts.yaml", f"declarations.{k} is not a declaration the kit knows")
        if v not in (None, True, False):
            rep.error("facts.yaml", f"declarations.{k} must be true, false or null")
    for k in ("privacy_policy_url", "account_deletion_url"):
        if f.get(k) is not None and not _url(f[k]):
            rep.error("facts.yaml", f"{k} must be an https:// URL")
    for d in _list_of(rep, "facts.yaml", f.get("target_devices"), "target_devices"):
        if d not in DEVICES:
            rep.error("facts.yaml", f"target_devices: unknown form factor {d!r} ({', '.join(sorted(DEVICES))})")
    return f


def check_project(kit, rep):
    p = _load(kit, "project.yaml", rep)
    if p is None:
        rep.warn("project.yaml", "missing: precheck needs --aab and build.py cannot build")
        return
    kind = p.get("kind")
    if kind not in KINDS:
        rep.error("project.yaml", f"kind must be one of {', '.join(sorted(KINDS))}")
    if kind == "external" and not p.get("output_glob"):
        rep.error("project.yaml", "kind external needs output_glob (the path of the signed AAB)")
    root = (kit / str(p.get("root", ".."))).resolve()
    if not root.is_dir():
        rep.error("project.yaml", f"root {root} does not exist")


def check_console(kit, rep, facts):
    c = _load(kit, "console.yaml", rep)
    if c is None:
        rep.warn("console.yaml", "missing: created by the bootstrap guide and `release.py import`")
        return
    if facts.get("package") and c.get("package") and c["package"] != facts["package"]:
        rep.error("console.yaml", f"package {c['package']} differs from facts.yaml {facts['package']}")
    sha = c.get("upload_certificate_sha256")
    if sha is not None and not SHA256_RE.match(str(sha)):
        rep.error("console.yaml", "upload_certificate_sha256 must be 32 hex bytes (colons allowed)")
    email = c.get("service_account_email")
    if email is not None and not str(email).endswith(".iam.gserviceaccount.com"):
        rep.error("console.yaml", "service_account_email must be a service account (…iam.gserviceaccount.com)")
    if c.get("publishing_mode") not in (None, *PUBLISHING):
        rep.error("console.yaml", "publishing_mode must be standard, managed or unknown")
    tracks = c.get("tracks") or {}
    imported = c.get("tracks_imported_at")
    ids = [v for k, v in tracks.items() if k != "closed" and v] + list((tracks.get("closed") or {}).values())
    if ids and not imported:
        rep.error("console.yaml", "track ids are set but tracks_imported_at is empty: run `release.py import`; "
                                  "a track id typed by hand (e.g. qa / internal) is not trusted")
    managed = c.get("managed") or {}
    for k in ("listings", "tracks"):
        if not isinstance(managed.get(k, []), list):
            rep.error("console.yaml", f"managed.{k} must be a list")
    known = set(ids)
    for t in managed.get("tracks", []) or []:
        if known and t not in known:
            rep.error("console.yaml", f"managed.tracks names {t!r}, which is not a discovered track id")


def check_testing(kit, rep):
    t = _load(kit, "testing-evidence.yaml", rep)
    if t is None:
        return
    for i, o in enumerate(_list_of(rep, "testing-evidence.yaml", t.get("observations"), "observations")):
        w = f"testing-evidence.yaml observations[{i}]"
        if not _is_date(o.get("date")):
            rep.error(w, "date must be YYYY-MM-DD")
        if not isinstance(o.get("testers_opted_in"), int) or o["testers_opted_in"] < 0:
            rep.error(w, "testers_opted_in must be a whole number")
        if o.get("source") not in ("console", "attested"):
            rep.error(w, "source must be console or attested")
    for i, d in enumerate(_list_of(rep, "testing-evidence.yaml", t.get("decisions"), "decisions")):
        w = f"testing-evidence.yaml decisions[{i}]"
        if not _is_date(d.get("date")):
            rep.error(w, "date must be YYYY-MM-DD")
        if d.get("outcome") not in ("granted", "denied"):
            rep.error(w, "outcome must be granted or denied")
    for i, a in enumerate(_list_of(rep, "testing-evidence.yaml", t.get("applications"), "applications")):
        if not _is_date(a.get("date")):
            rep.error(f"testing-evidence.yaml applications[{i}]", "date must be YYYY-MM-DD")


def check_decisions(kit, rep):
    d = _load(kit, "precheck-decisions.yaml", rep)
    if d is None:
        return
    for i, e in enumerate(_list_of(rep, "precheck-decisions.yaml", d.get("decisions"), "decisions")):
        missing = [k for k in ("rule", "reason", "date", "decided_by") if not e.get(k)]
        if missing:
            rep.error(f"precheck-decisions.yaml decisions[{i}]", f"missing {', '.join(missing)} (ignored by precheck)")


def check_extensions(kit, rep):
    d = _load(kit, "policy-extensions.yaml", rep)
    if d is None:
        return
    for i, e in enumerate(_list_of(rep, "policy-extensions.yaml", d.get("extensions"), "extensions")):
        w = f"policy-extensions.yaml extensions[{i}]"
        if e.get("kind") not in ("target_sdk", "billing_library"):
            rep.error(w, "kind must be target_sdk or billing_library")
        for k in ("until", "observed_at"):
            if not _is_date(e.get(k)):
                rep.error(w, f"{k} must be YYYY-MM-DD")
        if e.get("source") != "console":
            rep.warn(w, "only source: console lowers a BLOCKER; this entry is shown but not applied")


def check_history(kit, rep):
    h = _load(kit, "releases/release-history.yaml", rep)
    if h is None:
        return
    seen = set()
    for i, r in enumerate(_list_of(rep, "release-history.yaml", h.get("releases"), "releases")):
        vc = r.get("version_code")
        if not isinstance(vc, int) or vc <= 0:
            rep.error(f"release-history.yaml releases[{i}]", "version_code must be a positive whole number")
        elif (vc, r.get("track")) in seen:
            rep.warn(f"release-history.yaml releases[{i}]", f"version_code {vc} appears twice for one track")
        seen.add((vc, r.get("track")))


def check_schemas(kit, rep):
    facts = check_facts(kit, rep)
    check_project(kit, rep)
    check_console(kit, rep, facts)
    check_testing(kit, rep)
    check_decisions(kit, rep)
    check_extensions(kit, rep)
    check_history(kit, rep)


# ------------------------------------------------------------------------------------------------ secrets
def _git(kit, *args):
    try:
        r = subprocess.run(["git", "-C", str(kit), *args], capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return r.stdout if r.returncode == 0 else None


def check_secrets(kit, rep):
    top = _git(kit, "rev-parse", "--show-toplevel")
    if top:
        top = pathlib.Path(top.strip())
        for f in (_git(top, "ls-files") or "").splitlines():   # whole repo, not just the kit folder
            rel = f.strip()
            name = os.path.basename(rel)
            if any(fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch(name, pat) for pat in TRACKED_FORBIDDEN):
                rep.error("git", f"{rel} is tracked by git: run `git rm --cached {rel}` and keep it in .gitignore")
        private = kit / "private"
        if private.exists() and _git(kit, "check-ignore", "-q", str(private / "x")) is None:
            rep.error("git", f"{private} is not ignored: add the lines in templates/kit/gitignore.snippet")
    creds = kit / "private" / "credentials.yaml"
    if creds.exists():
        c = build.load_yaml(creds) or {}
        key = c.get("service_account_json")
        if key:
            path = pathlib.Path(os.path.expanduser(str(key))).resolve()
            if top and (path == top or top in path.parents):
                rep.error("private/credentials.yaml", "service_account_json points inside the repository; "
                                                      "move the key file outside it")
            elif not path.exists():
                rep.warn("private/credentials.yaml", f"service_account_json {path} does not exist")
    for name in GENERATED:
        p = kit / name
        if p.exists():
            for kind in scan_text(p.read_text(encoding="utf-8", errors="replace")):
                rep.error(name, f"contains {kind}: delete the file and regenerate it")


# ------------------------------------------------------------------------------------------------ init
def init(kit: pathlib.Path) -> list[str]:
    written = []
    for src in sorted(TEMPLATES.rglob("*")):
        if src.is_dir() or src.name == "gitignore.snippet":
            continue
        rel = src.relative_to(TEMPLATES)
        dest = kit / ("private/credentials.yaml" if rel.name == "credentials.example.yaml" else rel)
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        written.append(str(dest.relative_to(kit)))
    return written


def validate(kit, only=None) -> Report:
    kit = pathlib.Path(kit)
    rep = Report()
    if only in (None, "schemas"):
        check_schemas(kit, rep)
    if only in (None, "secrets"):
        check_secrets(kit, rep)
    return rep


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit", nargs="?", default="play-store-kit")
    ap.add_argument("--only", choices=("schemas", "secrets"))
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    kit = pathlib.Path(a.kit)
    if a.init:
        kit.mkdir(parents=True, exist_ok=True)
        written = init(kit)
        print("\n".join(f"wrote {w}" for w in written) or "nothing to write (files already exist)")
        print("add to .gitignore:\n" + (TEMPLATES / "gitignore.snippet").read_text())
        return 0
    rep = validate(kit, a.only)
    if a.json:
        print(json.dumps(rep.items, indent=2))
    else:
        for i in rep.items:
            print(f"{'✗' if i['level'] == 'error' else '!'} {i['where']}: {i['message']}")
        print(f"{len(rep.errors)} error(s), {len(rep.items) - len(rep.errors)} warning(s)")
    return 1 if rep.errors else 0


if __name__ == "__main__":
    sys.exit(main())
