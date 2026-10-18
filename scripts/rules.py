#!/usr/bin/env python3
"""Frozen Play rules with provenance (PLAN-FINAL.md §14).

  python3 scripts/rules.py freeze            # rules.src.yaml -> rules.json
  python3 scripts/rules.py freeze --check    # fail when rules.json is out of date
  python3 scripts/rules.py show [ID]         # print one rule or the table
  python3 scripts/rules.py check [--online]  # validate; --online re-fetches pages

Values are only ever written by `freeze` from the reviewed YAML. `check --online`
fetches each source page, records its sha256 next to the frozen one and reports
drift as an advisory review task (exit 0); it never rewrites a value.

Other scripts read rules through `load()` and `value(rules, id)`; an unverified
rule has value None and callers must treat it as unknown.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
SRC = HERE / "rules.src.yaml"
OUT = HERE / "rules.json"

FIELDS = (
    "id", "value", "unit", "applies_to", "effective_from", "effective_until",
    "source_url", "source_section", "fetched_at", "page_sha256",
    "reviewed_by", "review_decision", "status", "notes",
)
REQUIRED = ("id", "unit", "source_url", "source_section", "fetched_at", "reviewed_by", "review_decision", "status")
STATUSES = {"verified", "unverified"}
DECISIONS = {"accepted", "pending"}
ALLOWED_HOSTS = (
    "https://support.google.com/googleplay/",
    "https://developer.android.com/",
    "https://developers.google.com/android-publisher/",
    "https://developers.google.com/play/developer/reporting/",
    "https://play.google/",
    "https://google.play/",
    "https://android-developers.googleblog.com/",
)


class RuleError(ValueError):
    pass


def _validate(rules: list[dict]) -> list[str]:
    errors, seen = [], set()
    for r in rules:
        rid = r.get("id", "?")
        for f in REQUIRED:
            if r.get(f) in (None, ""):
                errors.append(f"{rid}: missing {f}")
        unknown = set(r) - set(FIELDS)
        if unknown:
            errors.append(f"{rid}: unknown field(s) {sorted(unknown)}")
        if rid in seen:
            errors.append(f"{rid}: duplicate id")
        seen.add(rid)
        status = r.get("status")
        if status not in STATUSES:
            errors.append(f"{rid}: status {status!r} not in {sorted(STATUSES)}")
        if status == "verified" and r.get("value") is None:
            errors.append(f"{rid}: verified rule without a value")
        if status == "unverified" and r.get("value") is not None:
            errors.append(f"{rid}: unverified rule must have value null")
        if r.get("review_decision") not in DECISIONS:
            errors.append(f"{rid}: review_decision {r.get('review_decision')!r} not in {sorted(DECISIONS)}")
        url = r.get("source_url") or ""
        if not url.startswith(ALLOWED_HOSTS):
            errors.append(f"{rid}: source_url {url!r} is not a Google primary source")
    return errors


def build_from_src(src: pathlib.Path = SRC) -> list[dict]:
    import yaml  # only freeze needs PyYAML; readers use rules.json

    doc = yaml.safe_load(src.read_text(encoding="utf-8"))
    defaults = doc.get("defaults", {})
    rules = []
    for raw in doc["rules"]:
        r = {**defaults, **raw}
        rules.append({f: r.get(f) for f in FIELDS})
    errors = _validate(rules)
    if errors:
        raise RuleError("\n".join(errors))
    return sorted(rules, key=lambda r: r["id"])


def render(rules: list[dict]) -> str:
    return json.dumps({"schema": "play-store-kit/rules/1", "rules": rules}, indent=2, ensure_ascii=False) + "\n"


def load(path: pathlib.Path = OUT) -> dict[str, dict]:
    doc = json.loads(path.read_text(encoding="utf-8"))
    rules = doc["rules"]
    errors = _validate(rules)
    if errors:
        raise RuleError("\n".join(errors))
    return {r["id"]: r for r in rules}


def value(rules: dict[str, dict], rid: str):
    """The rule's value, or None when it is unverified. Unknown ids raise KeyError."""
    return rules[rid]["value"]


def _fetch_sha256(url: str, timeout: float = 20.0) -> str | None:
    req = urllib.request.Request(url, headers={"User-Agent": "play-store-kit rules check"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return hashlib.sha256(resp.read()).hexdigest()
    except Exception:
        return None


def cmd_freeze(args) -> int:
    try:
        text = render(build_from_src())
    except RuleError as e:
        print(e, file=sys.stderr)
        return 1
    current = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if args.check:
        if current != text:
            print("rules.json is out of date; run: python3 scripts/rules.py freeze", file=sys.stderr)
            return 1
        print("rules.json is current")
        return 0
    if current != text:
        OUT.write_text(text, encoding="utf-8")
    print(f"rules.json: {text.count('\"id\":')} rules")
    return 0


def cmd_show(args) -> int:
    rules = load()
    if args.id:
        if args.id not in rules:
            print(f"no rule {args.id!r}", file=sys.stderr)
            return 1
        print(json.dumps(rules[args.id], indent=2, ensure_ascii=False))
        return 0
    for r in rules.values():
        v = "UNVERIFIED" if r["value"] is None else r["value"]
        print(f"{r['id']:<40} {v!s:<12} {r['unit']:<12} {r['source_section']}")
    return 0


def cmd_check(args) -> int:
    try:
        rules = load()
    except (RuleError, KeyError, json.JSONDecodeError) as e:
        print(f"rules.json invalid: {e}", file=sys.stderr)
        return 1
    print(f"rules.json: {len(rules)} rules valid")
    if not args.online:
        return 0
    hashes: dict[str, str | None] = {}
    for r in rules.values():
        url = r["source_url"]
        if url not in hashes:
            hashes[url] = _fetch_sha256(url)
        now, frozen = hashes[url], r["page_sha256"]
        if now is None:
            print(f"REVIEW {r['id']}: could not fetch {url}")
        elif frozen and now != frozen:
            print(f"REVIEW {r['id']}: page changed since {r['fetched_at']} ({url})")
        elif not frozen:
            print(f"INFO   {r['id']}: no frozen page hash; current {now[:12]} ({url})")
    print("Drift is advisory: re-read the page, update rules.src.yaml if a value changed, then freeze.")
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("freeze")
    f.add_argument("--check", action="store_true")
    f.set_defaults(func=cmd_freeze)
    s = sub.add_parser("show")
    s.add_argument("id", nargs="?")
    s.set_defaults(func=cmd_show)
    c = sub.add_parser("check")
    c.add_argument("--online", action="store_true")
    c.set_defaults(func=cmd_check)
    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
