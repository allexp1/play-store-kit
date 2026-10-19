#!/usr/bin/env python3
"""Approval receipts: one gate, one question, one receipt bound to one plan (PLAN-FINAL §4, §6.3).

  python3 scripts/approvals.py ask    KIT --gate G-UPLOAD          # the question to put to the person, verbatim
  python3 scripts/approvals.py record KIT --gate G-UPLOAD --text "the person's own words" [--channel chat|cli]
  python3 scripts/approvals.py list   KIT

A receipt is KIT/private/approvals/<timestamp>-<gate>.json:
  {gate, plan_id, fingerprint, effects, effect_sentence, scope_quoted, approved_text, approved_at, channel}
`record` is run only after the person answered the `ask` question in their own words; the skill never writes a
receipt on its own initiative and `--yes` is never a receipt. A command that needs a gate checks the receipt it is
given against the plan on disk (same gate, plan id and fingerprint, gate among the plan's effects) before it sends
anything to Google; any mismatch exits 2 with zero external writes.

The fingerprint is a sha256 over the canonical JSON of everything the approval is about: command, principal, app,
target, publishing mode, pending Console scope, remote baseline, artifacts, mutations, effects and rules version.
It does not cover the edit id or the plan state, which change while the plan runs.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sys
import time

import play_edit
from play_edit import EXIT_APPROVAL, EXIT_STALE, KitExit

FINGERPRINTED = ("plan_id", "command", "principal", "app", "target", "publishing_mode", "pending_console",
                 "remote_baseline", "artifacts", "mutations", "release", "transition", "effects", "rules_version")

GATES = {
    "G-UPLOAD": "The App Bundle {artifact} enters an edit as a draft release on track {track}. Its versionCode "
                "{version_code} may be used up even if the edit is later discarded.",
    "G-TEST-DIST": "Testers on track {track} can install {release} once the commit lands. The commit also sends "
                   "everything listed as pending in Play Console: {scope}.",
    "G-META-PUB": "Release notes{notes_langs} and track changes in this edit go live on approval ({mode} "
                  "publishing). Pending Console changes sent with it: {scope}.",
    "G-LISTING": "Store presence changes go live on approval ({mode} publishing): {summary}. Pending Console "
                 "changes sent with it: {scope}.",
    "G-PROD-SUBMIT": "Production release {release} ({status}{fraction}) is sent to Google for review and, under "
                     "{mode} publishing, {publish}. The commit also sends: {scope}.",
    "G-ROLLOUT": "On track {track}, release {release} changes from {before} to {after}{fallback}. "
                 "Pending Console changes sent with it: {scope}.",
}


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def fingerprint(plan: dict) -> str:
    return "sha256:" + hashlib.sha256(canonical({k: plan.get(k) for k in FINGERPRINTED}).encode()).hexdigest()


def scope_text(plan) -> str:
    pc = plan.get("pending_console") or {}
    items = pc.get("items")
    if items is None:
        return "UNKNOWN (record it first)"
    head = "nothing else" if not items else "; ".join(items)
    return f"{head} (as of {pc.get('observed_at')}, {pc.get('source')})"


def effect_sentence(plan: dict, gate: str) -> str:
    if gate not in GATES:
        raise KitExit(EXIT_APPROVAL, f"unknown gate {gate}")
    art = (plan.get("artifacts") or [{}])[0]
    rel = plan.get("release") or {}
    tr = plan.get("transition") or {}
    target = plan.get("target") or {}
    mode = plan.get("publishing_mode") or "unknown"
    langs = [n.get("language") for n in rel.get("releaseNotes") or []]
    fields = {
        "artifact": f"{pathlib.Path(art.get('path', '?')).name} (sha256 {str(art.get('sha256', '?'))[:12]}…, "
                    f"{art.get('package')} {art.get('version_name')} / {art.get('version_code')})",
        "track": target.get("track_id"), "version_code": art.get("version_code"),
        "release": rel.get("name") or tr.get("release"),
        "status": rel.get("status"), "fraction": f" to {rel['userFraction']:.0%} of users" if rel.get("userFraction")
        else "",
        "mode": mode,
        "publish": "it goes live as soon as Google approves it" if mode == "standard"
        else "it waits for you to press Publish changes" if mode == "managed" else "UNKNOWN",
        "scope": scope_text(plan), "notes_langs": f" ({', '.join(langs)})" if langs else "",
        "before": tr.get("before"), "after": tr.get("after"),
        "fallback": f"; release {tr['fallback']} keeps serving everyone else" if tr.get("fallback") else "",
    }
    fields["summary"] = "; ".join(target.get("summary") or [])
    if gate == "G-META-PUB" and plan.get("command") == "listing":
        return GATES["G-LISTING"].format(**fields)    # same gate, worded for listing plans
    return GATES[gate].format(**fields)


def question(plan: dict, gate: str) -> str:
    return (f"[{gate}] {effect_sentence(plan, gate)}\nPlan {plan['plan_id']} · {plan.get('fingerprint')}\n"
            "Do you approve exactly this? Answer in your own words.")


def approvals_dir(kit) -> pathlib.Path:
    return pathlib.Path(kit) / "private" / "approvals"


def record(kit, plan: dict, gate: str, approved_text: str, channel: str = "chat", now=time.time) -> pathlib.Path:
    text = (approved_text or "").strip()
    if not text or text.lower() in ("--yes", "-y"):
        raise KitExit(EXIT_APPROVAL, "a receipt needs the person's own words")
    if gate not in plan.get("effects", []):
        raise KitExit(EXIT_APPROVAL, f"{gate} is not an effect of plan {plan['plan_id']} ({plan.get('effects')})")
    if plan.get("fingerprint") != fingerprint(plan):
        raise KitExit(EXIT_STALE, "the plan on disk was edited after it was made; make a new plan")
    receipt = {"gate": gate, "plan_id": plan["plan_id"], "fingerprint": plan["fingerprint"],
               "effects": plan["effects"], "effect_sentence": effect_sentence(plan, gate),
               "scope_quoted": scope_text(plan), "approved_text": text, "approved_at": play_edit.utcnow(now()),
               "channel": channel}
    stamp = receipt["approved_at"].replace(":", "-")
    path = approvals_dir(kit) / f"{stamp}-{gate}.json"
    n = 1
    while path.exists():
        n += 1
        path = approvals_dir(kit) / f"{stamp}-{gate}-{n}.json"
    play_edit.write_atomic(path, json.dumps(receipt, indent=2, ensure_ascii=False) + "\n")
    return path


def check(kit, plan: dict, gate: str, receipt_paths) -> dict:
    """The receipt for `gate` among `receipt_paths`, verified against the plan; else KitExit(2). No network."""
    if plan.get("fingerprint") != fingerprint(plan):
        raise KitExit(EXIT_STALE, "the plan on disk does not match its fingerprint; make a new plan")
    if gate not in plan.get("effects", []):
        raise KitExit(EXIT_APPROVAL, f"{gate} is not an effect of this plan")
    base = approvals_dir(kit).resolve()
    seen = []
    for p in receipt_paths or []:
        p = pathlib.Path(p)
        try:
            r = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise KitExit(EXIT_APPROVAL, f"cannot read receipt {p}") from None
        if base not in p.resolve().parents:
            raise KitExit(EXIT_APPROVAL, f"receipt {p} is not in {base}")
        seen.append(r.get("gate"))
        if r.get("gate") != gate:
            continue
        if r.get("plan_id") != plan["plan_id"]:
            raise KitExit(EXIT_APPROVAL, f"receipt {p.name} is for plan {r.get('plan_id')}, not {plan['plan_id']}")
        if r.get("fingerprint") != plan["fingerprint"]:
            raise KitExit(EXIT_APPROVAL, f"receipt {p.name} approved a different version of this plan; ask again")
        if not str(r.get("approved_text", "")).strip():
            raise KitExit(EXIT_APPROVAL, f"receipt {p.name} has no approval text")
        return r
    raise KitExit(EXIT_APPROVAL, f"no {gate} receipt among {seen or 'the receipts given'}. Ask the person:\n"
                  + question(plan, gate))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("ask", "record", "list"))
    ap.add_argument("kit")
    ap.add_argument("--gate")
    ap.add_argument("--text")
    ap.add_argument("--channel", choices=("chat", "cli"), default="chat")
    a = ap.parse_args(argv)
    a.kit = str(pathlib.Path(a.kit).resolve())   # absolute from the start: git and writers never depend on cwd
    try:
        if a.cmd == "list":
            for p in sorted(approvals_dir(a.kit).glob("*.json")):
                r = json.loads(p.read_text(encoding="utf-8"))
                print(f"{p.name}: {r['gate']} plan {r['plan_id']} at {r['approved_at']}")
            return 0
        plan = play_edit.load_plan(a.kit)
        if not a.gate:
            ap.error("--gate is required")
        if a.cmd == "ask":
            print(question(plan, a.gate))
            return 0
        print(record(a.kit, plan, a.gate, a.text or "", a.channel))
        return 0
    except KitExit as e:
        print(e.message, file=sys.stderr)
        return e.code


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
