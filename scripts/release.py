#!/usr/bin/env python3
"""Releases on Google Play: import, plan, apply, commit, status, wait, rollout, halt, resume, promote, tracks,
reconcile, abort, bump (PLAN-FINAL §3.4, §4–§6, §8.3; contract in references/transactions-and-approvals.md).

  release.py import    KIT [--write-kit]                 # one read-only edit → console-live.json (+ track ids)
  release.py plan      KIT --aab PATH --track ROLE|ID --pending none|"item" [--pending "item" ...]
                           [--pending-source attested|console] [--status draft|completed|inProgress --fraction F]
                           [--notes releases/notes.yaml] [--priority 0-5] [--mapping PATH] [--native-symbols PATH]
  release.py apply     KIT --receipt private/approvals/…-G-UPLOAD.json
  release.py commit    KIT --receipt …-G-TEST-DIST.json|…-G-PROD-SUBMIT.json [--receipt …-G-META-PUB.json]
  release.py status    KIT [--track ROLE|ID]             # edit-free: releases.list per bound track
  release.py wait      KIT --version-code N [--track ROLE|ID] [--every 120] [--max-polls 180]
  release.py rollout   KIT --version-code N (--fraction F | --complete) --pending … [--receipt …-G-ROLLOUT.json]
  release.py halt      KIT --version-code N --pending … [--receipt …]
  release.py resume    KIT --version-code N [--fraction F] --pending … [--receipt …]
  release.py promote   KIT --from ROLE --version-code N --to ROLE [--status … --fraction F] --pending … [--receipt …]
  release.py tracks    KIT create --name NAME [--form-factor default|wear|automotive|tv] --pending … [--receipt …]
  release.py reconcile KIT
  release.py abort     KIT [--force]
  release.py bump      KIT [--version-name X]

Every mutating command is two steps. Without --receipt it writes KIT/play-plan.json (+ play-plan.md), prints the
approval question for each gate and exits 2 having sent nothing to Google. After the person answers,
`approvals.py record` stores their words as a receipt; the same command with --receipt then checks the receipt
against the plan (gate, plan id, fingerprint) before its first request. Plans are single-use. A changed artifact,
kit file or remote baseline means a new plan (exit 3 / 7), never an automatic rebase. `--pending` records what Play
Console lists as ready to send for review, because a commit sends those changes too; `none` means nothing.

Exit codes: 0 ok · 1 findings · 2 approval missing · 3 stale plan · 4 review conflict (edit kept) · 5 unknown outcome
(reconcile) · 6 lock held · 7 edit gone or baseline changed · 8 capability missing · 9 scope or mode unknown.
"""
from __future__ import annotations

import argparse
import copy
import json
import pathlib
import secrets
import sys
import time

import approvals
import build
import play_api
import play_edit
import precheck as precheck_mod
import rules as rules_mod
from play_api import PlayError, UnknownOutcome, UploadSessionGone
from play_edit import (EXIT_APPROVAL, EXIT_CAPABILITY, EXIT_FINDINGS, EXIT_GONE, EXIT_OK, EXIT_SCOPE, EXIT_STALE,
                       EXIT_UNKNOWN, EditOwner, Journal, KitExit)

ROOT = pathlib.Path(__file__).resolve().parent.parent
TESTING_GATE = {"production": "G-PROD-SUBMIT"}
LANDED_STATES = ("NOT_SENT_FOR_REVIEW", "IN_REVIEW", "APPROVED_NOT_PUBLISHED", "NOT_APPROVED", "PUBLISHED", "DRAFT")
TERMINAL = ("APPROVED_NOT_PUBLISHED", "NOT_APPROVED", "PUBLISHED")
NOTES_MAX = None   # filled from rules.json


def _skill_version():
    try:
        return json.loads((ROOT / "package.json").read_text())["version"]
    except (OSError, ValueError, KeyError):
        return "unknown"


def _rules_version():
    import hashlib
    return "sha256:" + hashlib.sha256((ROOT / "scripts" / "rules.json").read_bytes()).hexdigest()[:16]


def _canon(x):
    return approvals.canonical(x)


# ------------------------------------------------------------------------------------------------ track rules
def _has(r, vc):
    return str(vc) in [str(v) for v in r.get("versionCodes", [])]


def _find(releases, vc):
    for r in releases:
        if _has(r, vc):
            return r
    raise KitExit(EXIT_FINDINGS, f"no release with versionCode {vc} on this track")


def _describe(r):
    if not r:
        return "absent"
    f = r.get("userFraction")
    return f"{r.get('status')}" + (f" {f:.0%}" if f else "")


def merge_release(current: list, new: dict) -> list:
    """The track's releases after adding `new`, changing nothing else (a release with the same version codes is
    replaced; a completed release supersedes the previous completed one)."""
    out = [copy.deepcopy(r) for r in current if sorted(map(str, r.get("versionCodes", []))) !=
           sorted(map(str, new.get("versionCodes", [])))]
    if new.get("status") == "completed":
        out = [r for r in out if r.get("status") != "completed"]
    return out + [copy.deepcopy(new)]


def t_fraction(releases, vc, fraction):
    rel = copy.deepcopy(releases)
    r = _find(rel, vc)
    if r.get("status") != "inProgress":
        raise KitExit(EXIT_FINDINGS, f"release {vc} is {r.get('status')}, not inProgress; use resume for a halted one")
    if not (0 < fraction < 1):
        raise KitExit(EXIT_FINDINGS, "the fraction must be between 0 and 1 (use --complete for 100 %)")
    if fraction <= float(r.get("userFraction") or 0):
        raise KitExit(EXIT_FINDINGS, f"the rollout only goes up: {r.get('userFraction')} → {fraction} is not an "
                                     "increase (decreasing is not offered)")
    r["userFraction"] = fraction
    return rel


def t_complete(releases, vc):
    rel = copy.deepcopy(releases)
    r = _find(rel, vc)
    if r.get("status") not in ("inProgress",):
        raise KitExit(EXIT_FINDINGS, f"release {vc} is {r.get('status')}; only an inProgress rollout can be completed")
    r["status"] = "completed"
    r.pop("userFraction", None)
    return [x for x in rel if x is r or x.get("status") != "completed"]


def t_halt(releases, vc):
    rel = copy.deepcopy(releases)
    r = _find(rel, vc)
    if r.get("status") == "completed":
        raise KitExit(EXIT_FINDINGS, f"release {vc} is the completed release that serves everyone; the kit does not "
                                     "halt the serving fallback")
    if r.get("status") != "inProgress":
        raise KitExit(EXIT_FINDINGS, f"release {vc} is {r.get('status')}; only an inProgress rollout can be halted")
    if not any(x.get("status") == "completed" for x in rel if x is not r):
        raise KitExit(EXIT_FINDINGS, "no completed release on this track to fall back to")
    r["status"] = "halted"
    return rel


def t_resume(releases, vc, fraction=None):
    rel = copy.deepcopy(releases)
    r = _find(rel, vc)
    if r.get("status") != "halted":
        raise KitExit(EXIT_FINDINGS, f"release {vc} is {r.get('status')}, not halted")
    if fraction is not None:
        if not (float(r.get("userFraction") or 0) <= fraction < 1):
            raise KitExit(EXIT_FINDINGS, "resume keeps or raises the fraction, below 1")
        r["userFraction"] = fraction
    r["status"] = "inProgress"
    return rel


def t_promote(target_releases, source_release, status, fraction, production_live):
    new = {k: copy.deepcopy(v) for k, v in source_release.items() if k in ("name", "versionCodes", "releaseNotes")}
    new["status"] = status
    if status == "inProgress":
        if not production_live:
            raise KitExit(EXIT_FINDINGS, "the first release on this track cannot be a staged rollout; use completed or "
                                         "draft")
        if fraction is None or not 0 < fraction < 1:
            raise KitExit(EXIT_FINDINGS, "a staged promotion needs --fraction between 0 and 1")
        new["userFraction"] = fraction
    elif fraction is not None:
        raise KitExit(EXIT_FINDINGS, "--fraction only goes with --status inProgress")
    return merge_release(target_releases, new)


def fallback_of(releases, vc):
    for r in releases:
        if r.get("status") == "completed" and not _has(r, vc):
            return ",".join(map(str, r.get("versionCodes", [])))
    return None


# ------------------------------------------------------------------------------------------------ kit
class Kit:
    def __init__(self, path):
        self.path = pathlib.Path(path)
        self.facts = build.load_yaml(self.path / "facts.yaml") or {}
        self.console = build.load_yaml(self.path / "console.yaml") or {}
        p = self.path / "console-live.json"
        self.live = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}

    @property
    def package(self):
        pkg = self.console.get("package") or self.facts.get("package")
        if not pkg:
            raise KitExit(EXIT_FINDINGS, "no package in console.yaml or facts.yaml")
        return pkg

    def track(self, role_or_id) -> tuple[str, str]:
        """(track id, role) from console.yaml; ids are only those `import` discovered."""
        tracks = self.console.get("tracks") or {}
        if not self.console.get("tracks_imported_at"):
            raise KitExit(EXIT_STALE, "track ids are not discovered yet: run `release.py import KIT --write-kit`")
        closed = tracks.get("closed") or {}
        if role_or_id in ("production", "open", "internal"):
            tid = tracks.get(role_or_id)
            if not tid:
                raise KitExit(EXIT_FINDINGS, f"no {role_or_id} track was discovered for this app")
            return tid, role_or_id
        if role_or_id == "closed":
            if len(closed) != 1:
                raise KitExit(EXIT_FINDINGS, f"name the closed track: {', '.join(closed) or 'none discovered'}")
            return next(iter(closed.values())), "closed"
        for role in ("production", "open", "internal"):
            if tracks.get(role) == role_or_id:
                return role_or_id, role
        if role_or_id in closed.values() or role_or_id in closed:
            return closed.get(role_or_id, role_or_id), "closed"
        raise KitExit(EXIT_FINDINGS, f"{role_or_id!r} is not a discovered track ({_known_tracks(tracks)})")

    def baseline(self, track_id):
        t = (self.live.get("tracks") or {}).get(track_id)
        if t is None:
            raise KitExit(EXIT_STALE, f"no snapshot of track {track_id}: run `release.py import KIT`")
        return {"snapshot_id": self.live.get("snapshot_id"), "taken_at": self.live.get("captured_at"),
                "tracks": {track_id: {"releases": t.get("releases", [])}}}

    def publishing_mode(self):
        mode = self.console.get("publishing_mode") or "unknown"
        if mode not in ("standard", "managed"):
            raise KitExit(EXIT_SCOPE, "publishing mode is unknown: read Play Console → Publishing overview and set "
                                      "console.yaml publishing_mode to standard or managed")
        return mode


def _known_tracks(tracks):
    ids = [v for k, v in tracks.items() if k != "closed" and v] + list((tracks.get("closed") or {}).values())
    return ", ".join(ids) or "none"


def pending_scope(items, source, now):
    if not items:
        raise KitExit(EXIT_SCOPE, "say what Play Console lists as ready to send for review (Publishing overview): "
                                  "--pending none, or --pending \"<item>\" for each item. A commit sends those too.")
    items = [] if [i.lower() for i in items] == ["none"] else list(items)
    return {"source": source, "observed_at": play_edit.utcnow(now), "items": items}


def history_update(kit, version_code, track, plan_id=None, version_name=None, **stages):
    import yaml
    path = pathlib.Path(kit) / "releases" / "release-history.yaml"
    doc = build.load_yaml(path) or {}
    rel = doc.setdefault("releases", [])
    for e in rel:
        if e.get("version_code") == version_code and e.get("track") == track:
            break
    else:
        e = {"version_code": version_code, "version_name": version_name, "track": track, "plan_id": plan_id,
             "stages": {}}
        rel.append(e)
    if plan_id:
        e["plan_id"] = plan_id
    st = e.setdefault("stages", {}) or {}
    e["stages"] = st
    for k, v in stages.items():
        st.setdefault(k, v)
    play_edit.write_atomic(path, "# Written by scripts/release.py; edit only attested fields.\n"
                           + yaml.safe_dump(json.loads(json.dumps(doc, default=str)), sort_keys=False))


# ------------------------------------------------------------------------------------------------ runner
class Runner:
    def __init__(self, kit, client=None, sa=None, now=time.time, sleep=time.sleep, inspect=None, precheck=None,
                 out=print):
        self.kit = Kit(kit)
        self.client, self.sa, self.now, self.sleep = client, sa, now, sleep
        self.inspect = inspect or (lambda path, **kw: build.inspect(path, **kw))
        self.precheck = precheck or (lambda kit, rec: precheck_mod.run(kit, rec))
        self.out = out

    # -- plumbing
    def principal(self):
        sa = self.sa or play_api.resolve_credentials(self.kit.path)
        return {"client_email": sa.client_email, "key_id": sa.private_key_id, "fingerprint": sa.fingerprint}

    def owner(self):
        return EditOwner(self.client, self.kit.path, self.now)

    def _new_plan(self, command, **fields):
        stamp = play_edit.utcnow(self.now()).replace(":", "-")
        pid = f"{stamp}-{secrets.token_hex(2)}"
        plan = {"schema": 1, "plan_id": pid, "created_at": play_edit.utcnow(self.now()),
                "skill_version": _skill_version(), "rules_version": _rules_version(), "command": command,
                "principal": self.principal(),
                "app": {"package": self.kit.package,
                        "developer_account_id": self.kit.console.get("developer_account_id")},
                "artifacts": [], "mutations": [], "approvals": [], "edit": {"id": None, "expiry_time_seconds": None},
                "state": "planned", "journal": f"private/journal/{pid}.jsonl", "progress": {}, **fields}
        plan["fingerprint"] = approvals.fingerprint(plan)
        return plan

    def _save_and_ask(self, plan, gates=None):
        cur = self.kit.path / play_edit.PLAN_FILE
        if cur.exists():
            old = json.loads(cur.read_text(encoding="utf-8"))
            if old.get("state") in ("applying", "applied", "committing", "unknown"):
                raise KitExit(EXIT_STALE, f"plan {old['plan_id']} is {old['state']}: commit, reconcile or abort it "
                                          "before making a new one")
        play_edit.save_plan(self.kit.path, plan)
        play_edit.write_atomic(self.kit.path / "play-plan.md", render_plan(plan))
        qs = "\n\n".join(approvals.question(plan, g) for g in (gates or plan["effects"]))
        self.out(f"plan {plan['plan_id']} written. Approval needed before anything is sent to Google:\n\n{qs}\n\n"
                 "Record the person's answer with:\n  python3 scripts/approvals.py record KIT --gate <GATE> --text "
                 "\"<their words>\"")
        return plan

    def _receipts(self, plan, gates, receipts):
        for g in gates:
            r = approvals.check(self.kit.path, plan, g, receipts)
            ref = {"gate": g, "receipt": r.get("approved_at"), "fingerprint": r["fingerprint"]}
            if ref not in plan["approvals"]:
                plan["approvals"].append(ref)

    def _need_client(self):
        if self.client is None:
            raise KitExit(EXIT_CAPABILITY, "no API client (credentials missing)")

    def _journal(self, plan):
        return Journal(self.kit.path / plan["journal"], self.now)

    def _sessions_path(self, plan):
        return self.kit.path / "private" / "sessions" / f"{plan['plan_id']}.json"

    def _session(self, plan, op, url=None):
        p = self._sessions_path(plan)
        data = json.loads(p.read_text()) if p.exists() else {}
        if url is not None:
            data[op] = url
            play_edit.write_atomic(p, json.dumps(data))
        return data.get(op)

    def _params(self, plan, **extra):
        return {"packageName": plan["app"]["package"], "editId": plan["edit"]["id"], **extra}

    def _baseline_check(self, plan, track_id):
        want = plan["remote_baseline"]["tracks"][track_id]["releases"]
        got = self.client.call("edits.tracks.get", self._params(plan, track=track_id)).get("releases", [])
        if _canon(want) != _canon(got):
            journal = self._journal(plan)
            journal.append(op="baseline", phase="failed", method="edits.tracks.get", edit_id=plan["edit"]["id"],
                           reason="remote track changed since the plan")
            plan["state"] = "invalidated"
            play_edit.save_plan(self.kit.path, plan)
            play_edit.Lock(self.kit.path, plan["principal"]["fingerprint"], plan["app"]["package"],
                           self.now).release(plan["plan_id"])
            raise KitExit(EXIT_GONE, f"track {track_id} changed since the plan was made (Console or another client); "
                                     "run import and make a new plan",
                          {"planned": [_describe(r) for r in want], "now": [_describe(r) for r in got]})
        return got

    def _open_or_resume(self, plan):
        owner = self.owner()
        if plan["edit"].get("id"):
            owner.resume(plan)
        else:
            owner.open(plan)
        plan["state"] = "applying"
        play_edit.save_plan(self.kit.path, plan)
        return owner

    def _unknown(self, plan, what):
        plan["state"] = "unknown"
        play_edit.save_plan(self.kit.path, plan)
        raise KitExit(EXIT_UNKNOWN, f"{what}: the outcome is unknown. Run `release.py reconcile KIT` before "
                                    "anything else; nothing is retried blind")

    # ------------------------------------------------------------------------------------------- import
    def cmd_import(self, write_kit=False):
        self._need_client()
        cur = None
        p = self.kit.path / play_edit.PLAN_FILE
        if p.exists():
            cur = json.loads(p.read_text(encoding="utf-8"))
        reuse = cur if cur and cur.get("state") in ("applying", "applied") else None
        plan_like = reuse or {"plan_id": "import-" + play_edit.utcnow(self.now()).replace(":", "-"),
                              "app": {"package": self.kit.package}, "principal": self.principal()}
        pkg = self.kit.package
        snap = {"captured_at": play_edit.utcnow(self.now()), "package": pkg}
        with self.owner().readonly_edit(plan_like) as (edit_id, reused):
            par = {"packageName": pkg, "editId": edit_id}
            tracks = self.client.call("edits.tracks.list", par).get("tracks", [])
            snap["tracks"] = {t["track"]: {"releases": t.get("releases", [])} for t in tracks}
            snap["listings"] = self.client.call("edits.listings.list", par).get("listings", [])
            snap["details"] = self.client.call("edits.details.get", par)
            snap["testers"], snap["countries"] = {}, {}
            for t in snap["tracks"]:
                for key, mid in (("testers", "edits.testers.get"), ("countries", "edits.countryavailability.get")):
                    try:
                        snap[key][t] = self.client.call(mid, {**par, "track": t})
                    except PlayError as e:
                        snap[key][t] = {"_error": e.status}
            snap["view"] = "includes uncommitted changes of plan " + plan_like["plan_id"] if reused else "committed"
        snap["snapshot_id"] = "sha256:" + __import__("hashlib").sha256(_canon(snap).encode()).hexdigest()[:16]
        snaps = self.kit.path / "private" / "snapshots"
        play_edit.write_atomic(snaps / f"{snap['captured_at'].replace(':', '-')}.json", json.dumps(snap, indent=2))
        live = {**self.kit.live, **{k: snap[k] for k in ("captured_at", "snapshot_id", "tracks", "view")}}
        live["listing_languages"] = sorted(x.get("language") for x in snap["listings"] if x.get("language"))
        play_edit.write_atomic(self.kit.path / "console-live.json", json.dumps(live, indent=2) + "\n")
        self.kit.live = live
        if write_kit:
            self._write_tracks(list(snap["tracks"]), snap["captured_at"])
        self.out(f"imported {len(snap['tracks'])} track(s): {', '.join(snap['tracks'])} ({snap['view']})")
        return snap

    def _write_tracks(self, ids, when):
        import yaml
        roles = {"production": "production" if "production" in ids else None,
                 "open": "beta" if "beta" in ids else None,
                 "internal": next((t for t in ("qa", "internal") if t in ids), None)}
        used = {v for v in roles.values() if v}
        roles["closed"] = {t: t for t in ids if t not in used}
        console = dict(self.kit.console)
        console["tracks"] = roles
        console["tracks_imported_at"] = when
        play_edit.write_atomic(self.kit.path / "console.yaml",
                               "# Track ids written by `release.py import --write-kit`.\n"
                               + yaml.safe_dump(console, sort_keys=False, allow_unicode=True))
        self.kit.console = console

    # ------------------------------------------------------------------------------------------- plan
    def cmd_plan(self, aab, track, pending, pending_source="attested", status="draft", fraction=None, notes=None,
                 priority=None, mapping=None, native_symbols=None):
        track_id, role = self.kit.track(track)
        mode = self.kit.publishing_mode()
        scope = pending_scope(pending, pending_source, self.now())
        rec = self.inspect(aab, mapping=mapping, native_symbols=native_symbols)
        pkg = self.kit.package
        if rec.package != pkg:
            raise KitExit(EXIT_FINDINGS, f"the AAB is {rec.package}, the kit is {pkg}")
        findings = [f for f in self.precheck(self.kit.path, rec)
                    if f.counts() and not (f.id == "W-TESTING-GATE" and role != "production")]
        if findings:
            raise KitExit(EXIT_FINDINGS, "precheck has blocking findings; fix them (see `precheck.py`)",
                          [f"{f.id} {f.severity}: {f.message}" for f in findings])
        baseline = self.kit.baseline(track_id)
        seen = {int(v) for t in (self.kit.live.get("tracks") or {}).values() for r in t.get("releases", [])
                for v in r.get("versionCodes", [])}
        if seen and rec.version_code <= max(seen):
            raise KitExit(EXIT_FINDINGS, f"versionCode {rec.version_code} is not above {max(seen)}, the highest Play "
                                         "has on any track: bump it")
        releases = baseline["tracks"][track_id]["releases"]
        production_live = any(r.get("status") in ("completed", "inProgress", "halted") for r in releases)
        if status not in ("draft", "completed", "inProgress"):
            raise KitExit(EXIT_FINDINGS, "status is draft, completed or inProgress")
        if status == "inProgress" and not (fraction and 0 < fraction < 1):
            raise KitExit(EXIT_FINDINGS, "inProgress needs --fraction between 0 and 1")
        if status != "inProgress" and fraction is not None:
            raise KitExit(EXIT_FINDINGS, "--fraction only goes with --status inProgress")
        if status == "inProgress" and role == "production" and not production_live:
            raise KitExit(EXIT_FINDINGS, "the first production release cannot be a staged rollout: use draft or "
                                         "completed")
        if priority is not None and not 0 <= priority <= 5:
            raise KitExit(EXIT_FINDINGS, "--priority is 0 to 5")
        release = {"name": f"{rec.version_name} ({rec.version_code})", "versionCodes": [str(rec.version_code)],
                   "status": status}
        if fraction:
            release["userFraction"] = fraction
        if priority is not None:
            release["inAppUpdatePriority"] = priority
        if notes:
            release["releaseNotes"] = load_notes(self.kit.path, notes)
        art = {"path": str(pathlib.Path(aab).resolve()), "sha256": rec.sha256, "package": rec.package,
               "version_code": rec.version_code, "version_name": rec.version_name, "signer_sha256": rec.signer_sha256,
               "size_bytes": rec.size_bytes, "mapping_path": rec.mapping_path,
               "native_symbols_path": rec.native_symbols_path}
        muts = [{"seq": 1, "method": "edits.bundles.upload", "class": "reconcilable", "body_ref": "artifacts[0]"}]
        if rec.mapping_path:
            muts.append({"seq": len(muts) + 1, "method": "edits.deobfuscationfiles.upload", "type": "proguard",
                         "class": "reconcilable", "body_sha256": build.sha256_file(rec.mapping_path)})
        if rec.native_symbols_path:
            muts.append({"seq": len(muts) + 1, "method": "edits.deobfuscationfiles.upload", "type": "nativeCode",
                         "class": "reconcilable", "body_sha256": build.sha256_file(rec.native_symbols_path)})
        muts.append({"seq": len(muts) + 1, "method": "edits.tracks.update", "class": "idempotent",
                     "track": track_id, "release": release})
        effects = ["G-UPLOAD"]
        if status != "draft":
            effects.append(TESTING_GATE.get(role, "G-TEST-DIST"))
        if notes or (status == "draft" and scope["items"]):
            effects.append("G-META-PUB")
        plan = self._new_plan("release", target={"track_id": track_id, "track_role": role, "form_factor":
                              track_id.split(":")[0] if ":" in track_id else "default"},
                              publishing_mode=mode, pending_console=scope, remote_baseline=baseline,
                              artifacts=[art], release=release, mutations=muts, effects=effects)
        return self._save_and_ask(plan)

    # ------------------------------------------------------------------------------------------- apply
    def cmd_apply(self, receipts):
        plan = play_edit.load_plan(self.kit.path)
        if plan.get("command") != "release":
            raise KitExit(EXIT_STALE, f"the kit's plan is a {plan.get('command')} plan; apply is for release plans")
        if plan["state"] not in ("planned", "applying"):
            raise KitExit(EXIT_STALE, f"plan {plan['plan_id']} is {plan['state']}; plans are single-use")
        self._receipts(plan, ["G-UPLOAD"], receipts)          # before any request
        art = plan["artifacts"][0]
        if build.sha256_file(art["path"]) != art["sha256"]:
            raise KitExit(EXIT_STALE, "the AAB changed since the plan was made; make a new plan")
        self._need_client()
        play_edit.save_plan(self.kit.path, plan)
        self._open_or_resume(plan)
        prog = plan["progress"]
        track_id = plan["target"]["track_id"]
        journal = self._journal(plan)
        if not prog.get("baseline"):
            self._baseline_check(plan, track_id)
            prog["baseline"] = play_edit.utcnow(self.now())
            play_edit.save_plan(self.kit.path, plan)
        if not prog.get("bundle"):
            prog["bundle"] = self._upload_bundle(plan, journal)
            play_edit.save_plan(self.kit.path, plan)
            history_update(self.kit.path, art["version_code"], track_id, plan["plan_id"], art["version_name"],
                           bound=plan["created_at"], uploaded=play_edit.utcnow(self.now()))
        for m in plan["mutations"]:
            if m["method"] == "edits.deobfuscationfiles.upload" and not prog.get(m["type"]):
                prog[m["type"]] = self._upload_symbols(plan, journal, m["type"])
                play_edit.save_plan(self.kit.path, plan)
        if not prog.get("track"):
            self._update_track(plan, journal, track_id, plan["release"])
            prog["track"] = play_edit.utcnow(self.now())
            play_edit.save_plan(self.kit.path, plan)
        self.owner().validate(plan)
        plan["state"] = "applied"
        play_edit.save_plan(self.kit.path, plan)
        history_update(self.kit.path, art["version_code"], track_id, draft=play_edit.utcnow(self.now()))
        self.out(f"applied: {plan['release']['name']} is in edit {plan['edit']['id']} on {track_id} (not sent yet). "
                 f"Next: commit with the {', '.join(g for g in plan['effects'] if g != 'G-UPLOAD') or 'G-UPLOAD'} "
                 "receipt.")
        return plan

    def _upload_bundle(self, plan, journal):
        art = plan["artifacts"][0]
        op = "edits.bundles.upload"
        try:
            bundle = journal.run(op, op, lambda: self.client.upload_resumable(
                op, self._params(plan), art["path"], on_session=lambda u: self._session(plan, op, u)),
                edit_id=plan["edit"]["id"], summarize=lambda b: {"versionCode": b.get("versionCode"),
                                                                 "sha256": b.get("sha256")})
        except (UnknownOutcome, UploadSessionGone):
            journal.append(op=op, phase="unknown", method=op, edit_id=plan["edit"]["id"], reason="session lost")
            self._unknown(plan, "the bundle upload lost its response")
        except PlayError as e:
            if e.has("apkUpgradeVersionConflict") or "already been used" in e.message:
                plan["state"] = "invalidated"
                play_edit.save_plan(self.kit.path, plan)
                raise KitExit(EXIT_FINDINGS, f"Play already has versionCode {art['version_code']}: bump it and make a "
                                             "new plan") from None
            raise
        return self._bind(plan, bundle)

    def _bind(self, plan, bundle):
        art = plan["artifacts"][0]
        if str(bundle.get("sha256", "")).lower() != art["sha256"].lower() or \
                int(bundle.get("versionCode", -1)) != int(art["version_code"]):
            plan["state"] = "invalidated"
            play_edit.save_plan(self.kit.path, plan)
            raise KitExit(EXIT_FINDINGS, "Google's copy of the bundle does not match the planned artifact",
                          {"planned": [art["version_code"], art["sha256"]],
                           "google": [bundle.get("versionCode"), bundle.get("sha256")]})
        return {"versionCode": bundle["versionCode"], "sha256": bundle["sha256"]}

    def _upload_symbols(self, plan, journal, kind):
        art = plan["artifacts"][0]
        path = art["mapping_path"] if kind == "proguard" else art["native_symbols_path"]
        op = f"edits.deobfuscationfiles.upload:{kind}"
        mid = "edits.deobfuscationfiles.upload"
        try:
            journal.run(op, mid, lambda: self.client.upload_resumable(
                mid, self._params(plan, apkVersionCode=art["version_code"], deobfuscationFileType=kind), path,
                on_session=lambda u: self._session(plan, op, u)), edit_id=plan["edit"]["id"])
        except (UnknownOutcome, UploadSessionGone):
            journal.append(op=op, phase="unknown", method=mid, edit_id=plan["edit"]["id"], reason="session lost")
            self._unknown(plan, f"the {kind} upload lost its response")
        return play_edit.utcnow(self.now())

    def _update_track(self, plan, journal, track_id, new_release=None, releases=None):
        current = self.client.call("edits.tracks.get", self._params(plan, track=track_id)).get("releases", [])
        body = {"track": track_id, "releases": releases if releases is not None else merge_release(current, new_release)}
        plan["progress"]["track_body"] = body
        play_edit.save_plan(self.kit.path, plan)

        def confirm():
            try:
                self.client.call("edits.get", self._params(plan))
                return True
            except (PlayError, UnknownOutcome):
                return False
        try:
            return journal.run("edits.tracks.update", "edits.tracks.update", lambda: self.client.call(
                "edits.tracks.update", self._params(plan, track=track_id), body=body, confirm=confirm),
                edit_id=plan["edit"]["id"])
        except UnknownOutcome:
            self._unknown(plan, "the track update lost its response")

    # ------------------------------------------------------------------------------------------- commit
    def cmd_commit(self, receipts):
        plan = play_edit.load_plan(self.kit.path)
        if plan["state"] != "applied":
            raise KitExit(EXIT_STALE, f"plan {plan['plan_id']} is {plan['state']}; commit needs an applied plan")
        gates = [g for g in plan["effects"] if g != "G-UPLOAD"] or ["G-UPLOAD"]
        self._receipts(plan, gates, receipts)                 # before any request
        if (plan.get("pending_console") or {}).get("items") is None:
            raise KitExit(EXIT_SCOPE, "the plan has no recorded Console scope")
        self._need_client()
        play_edit.save_plan(self.kit.path, plan)
        return self._commit(plan)

    def _commit(self, plan):
        self.owner().commit(plan)
        tr = plan["target"]["track_id"]
        if plan.get("command") == "release":
            art = plan["artifacts"][0]
            history_update(self.kit.path, art["version_code"], tr, submitted=plan.get("committed_at"))
        self.out(f"committed plan {plan['plan_id']} on {tr}. Follow it with `release.py status KIT` / `wait`.")
        return plan

    # ------------------------------------------------------------------------------------------- transitions
    def _transition(self, command, track, vc, kind, pending, pending_source, receipts, **kw):
        """rollout / halt / resume / promote: plan without a receipt, execute with one."""
        if receipts:
            plan = play_edit.load_plan(self.kit.path)
            fresh = self._transition_plan(command, track, vc, kind, pending, pending_source, keep=plan, **kw)
            if plan.get("command") != command or plan["state"] not in ("planned", "applying", "applied") or \
                    fresh["fingerprint"] != plan["fingerprint"]:
                raise KitExit(EXIT_STALE, "the saved plan is not this transition (or the kit changed since); run the "
                                          "command without --receipt to make a new plan")
            self._receipts(plan, plan["effects"], receipts)    # before any request
            self._need_client()
            return self._execute(plan)
        plan = self._transition_plan(command, track, vc, kind, pending, pending_source, **kw)
        self._save_and_ask(plan)
        raise KitExit(EXIT_APPROVAL, "approval needed (plan saved; nothing was sent)")

    def _transition_plan(self, command, track, vc, kind, pending, pending_source, keep=None, fraction=None,
                         source=None, status=None):
        track_id, role = self.kit.track(track)
        mode = self.kit.publishing_mode()
        scope = (keep or {}).get("pending_console") if keep else pending_scope(pending, pending_source, self.now())
        baseline = self.kit.baseline(track_id)
        current = baseline["tracks"][track_id]["releases"]
        src = None
        if kind == "promote":
            src_id, _ = self.kit.track(source)
            src_rel = (self.kit.live.get("tracks") or {}).get(src_id, {}).get("releases", [])
            src = copy.deepcopy(_find(src_rel, vc))
            baseline["tracks"][src_id] = {"releases": src_rel}
            live = any(r.get("status") in ("completed", "inProgress", "halted") for r in current)
            after = t_promote(current, src, status or "draft", fraction, live)
            gate = "G-PROD-SUBMIT" if role == "production" else "G-TEST-DIST"
        else:
            after = {"fraction": lambda: t_fraction(current, vc, fraction), "complete": lambda: t_complete(current, vc),
                     "halt": lambda: t_halt(current, vc), "resume": lambda: t_resume(current, vc, fraction)}[kind]()
            gate = "G-ROLLOUT"
        before_r = next((r for r in current if _has(r, vc)), None)
        after_r = next((r for r in after if _has(r, vc)), None)
        transition = {"kind": kind, "release": (after_r or {}).get("name") or str(vc), "version_code": vc,
                      "before": _describe(before_r), "after": _describe(after_r),
                      "fallback": fallback_of(after, vc) if kind in ("halt", "fraction", "resume") else None,
                      "source_track": source, "releases_after": after}
        fields = dict(target={"track_id": track_id, "track_role": role, "form_factor": "default"},
                      publishing_mode=mode, pending_console=scope, remote_baseline=baseline, transition=transition,
                      mutations=[{"seq": 1, "method": "edits.tracks.update", "class": "idempotent", "track": track_id,
                                  "releases": after}, {"seq": 2, "method": "edits.commit", "class": "reconcilable"}],
                      effects=[gate])
        if keep:
            plan = {**keep, **fields}
            plan["fingerprint"] = approvals.fingerprint(plan)
            return plan
        return self._new_plan(command, **fields)

    def _execute(self, plan):
        track_id = plan["target"]["track_id"]
        journal = self._journal(plan)
        if plan["state"] != "applied":
            self._open_or_resume(plan)
            prog = plan["progress"]
            if not prog.get("baseline"):
                if plan["remote_baseline"].get("absent"):
                    if _exists_track(self.client, self._params(plan), track_id):
                        raise KitExit(EXIT_GONE, f"track {track_id} exists now; run import")
                else:
                    self._baseline_check(plan, track_id)
                src = plan["transition"].get("source_track")
                if src:
                    sid, _ = self.kit.track(src)
                    want = plan["remote_baseline"]["tracks"][sid]["releases"]
                    got = self.client.call("edits.tracks.get", self._params(plan, track=sid)).get("releases", [])
                    if _canon(want) != _canon(got):
                        raise KitExit(EXIT_GONE, f"source track {sid} changed since the plan; make a new plan")
                prog["baseline"] = play_edit.utcnow(self.now())
                play_edit.save_plan(self.kit.path, plan)
            if not prog.get("track"):
                if plan["command"] == "tracks-create":
                    self._create_track(plan, journal)
                else:
                    self._update_track(plan, journal, track_id, releases=plan["transition"]["releases_after"])
                prog["track"] = play_edit.utcnow(self.now())
                play_edit.save_plan(self.kit.path, plan)
            self.owner().validate(plan)
            plan["state"] = "applied"
            play_edit.save_plan(self.kit.path, plan)
        return self._commit(plan)

    # ------------------------------------------------------------------------------------------- tracks create
    def cmd_tracks_create(self, name, form_factor, pending, pending_source, receipts):
        tid = name if form_factor in (None, "default") else f"{form_factor}:{name}"
        if receipts:
            plan = play_edit.load_plan(self.kit.path)
            if plan.get("command") != "tracks-create" or plan["target"]["track_id"] != tid:
                raise KitExit(EXIT_STALE, "the saved plan is not this track creation; run without --receipt first")
            self._receipts(plan, plan["effects"], receipts)
            self._need_client()
            return self._execute(plan)
        if not self.kit.console.get("tracks_imported_at"):
            raise KitExit(EXIT_STALE, "run `release.py import KIT --write-kit` first")
        if tid in (self.kit.live.get("tracks") or {}):
            raise KitExit(EXIT_FINDINGS, f"track {tid} already exists")
        mode = self.kit.publishing_mode()
        plan = self._new_plan("tracks-create", target={"track_id": tid, "track_role": "closed",
                                                       "form_factor": form_factor or "default"},
                              publishing_mode=mode, pending_console=pending_scope(pending, pending_source, self.now()),
                              remote_baseline={"snapshot_id": self.kit.live.get("snapshot_id"),
                                               "tracks": {tid: {"releases": []}}, "absent": True},
                              transition={"kind": "create", "release": None, "before": "absent",
                                          "after": "closed testing track", "releases_after": []},
                              mutations=[{"seq": 1, "method": "edits.tracks.create", "class": "reconcilable",
                                          "body": {"track": name, "type": "closedTesting",
                                                   **({"formFactor": form_factor.upper()} if form_factor and
                                                      form_factor != "default" else {})}},
                                         {"seq": 2, "method": "edits.commit", "class": "reconcilable"}],
                              effects=["G-META-PUB"])
        self._save_and_ask(plan)
        raise KitExit(EXIT_APPROVAL, "approval needed (plan saved; nothing was sent)")

    def _create_track(self, plan, journal):
        body = plan["mutations"][0]["body"]
        try:
            journal.run("edits.tracks.create", "edits.tracks.create", lambda: self.client.call(
                "edits.tracks.create", self._params(plan), body=body), edit_id=plan["edit"]["id"])
        except UnknownOutcome:
            self._unknown(plan, "the track creation lost its response")

    # ------------------------------------------------------------------------------------------- status / wait
    def bound_tracks(self):
        t = self.kit.console.get("tracks") or {}
        ids = [v for k, v in t.items() if k != "closed" and v] + list((t.get("closed") or {}).values())
        if not ids:
            raise KitExit(EXIT_STALE, "no track ids: run `release.py import KIT --write-kit`")
        return ids

    def cmd_status(self, track=None):
        self._need_client()
        ids = [self.kit.track(track)[0]] if track else self.bound_tracks()
        now = play_edit.utcnow(self.now())
        status = dict(self.kit.live.get("releases_status") or {})
        for tid in ids:
            rel = self.client.releases(self.kit.package, tid)
            status[tid] = {"checked_at": now, "releases": [
                {"releaseName": r.get("releaseName"), "releaseLifecycleState": r.get("releaseLifecycleState"),
                 "versionCodes": [str(a.get("versionCode")) for a in r.get("activeArtifacts", [])]} for r in rel]}
            for r in status[tid]["releases"]:
                state = str(r["releaseLifecycleState"]).replace("RELEASE_LIFECYCLE_STATE_", "")
                stage = {"IN_REVIEW": "in_review", "PUBLISHED": "published"}.get(state)
                for vc in r["versionCodes"]:
                    if stage and _in_history(self.kit.path, int(vc), tid):
                        history_update(self.kit.path, int(vc), tid, **{stage: now})
        live = {**self.kit.live, "releases_status": status}
        play_edit.write_atomic(self.kit.path / "console-live.json", json.dumps(live, indent=2) + "\n")
        self.kit.live = live
        lines = []
        for tid in ids:
            for r in status[tid]["releases"] or [{"releaseName": "—", "releaseLifecycleState": "no releases",
                                                   "versionCodes": []}]:
                lines.append(f"{tid:<14} {','.join(r['versionCodes']) or '-':<10} {str(r['releaseName']):<24} "
                             f"{str(r['releaseLifecycleState']).replace('RELEASE_LIFECYCLE_STATE_', '')}")
        p = self.kit.path / play_edit.PLAN_FILE
        if p.exists():
            plan = json.loads(p.read_text(encoding="utf-8"))
            un = self._journal(plan).unresolved()
            lines.append(f"plan {plan['plan_id']}: {plan['state']}" + (f", edit {plan['edit']['id']}"
                                                                         if plan["edit"].get("id") else "")
                         + (f", {len(un)} unknown outcome(s): run reconcile" if un else ""))
        self.out("\n".join(lines))
        return status

    def cmd_wait(self, version_code, track=None, every=120, max_polls=180):
        self._need_client()
        if track:
            tid = self.kit.track(track)[0]
        else:
            plan = play_edit.load_plan(self.kit.path)
            tid = plan["target"]["track_id"]
        last = None
        for i in range(max_polls):
            state = None
            for r in self.client.releases(self.kit.package, tid):
                if int(version_code) in [int(a.get("versionCode")) for a in r.get("activeArtifacts", [])]:
                    state = str(r.get("releaseLifecycleState")).replace("RELEASE_LIFECYCLE_STATE_", "")
            if state != last:
                self.out(f"{play_edit.utcnow(self.now())} {tid} {version_code}: {state or 'not on the track'}")
                last = state
            if state in TERMINAL:
                return state
            if i + 1 < max_polls:
                self.sleep(every)
        return last

    # ------------------------------------------------------------------------------------------- reconcile / abort
    def cmd_reconcile(self):
        plan = play_edit.load_plan(self.kit.path)
        journal = self._journal(plan)
        open_ops = journal.unresolved()
        if not open_ops:
            self.out(f"nothing to reconcile (plan {plan['state']})")
            return plan
        self._need_client()
        results = {}
        for e in open_ops:
            op = e["op"]
            if op == "edits.commit":
                results[op] = self.owner().reconcile_commit(plan, lambda: self._landed(plan))
            elif op.startswith(("edits.bundles.upload", "edits.deobfuscationfiles.upload")):
                results[op] = self._reconcile_upload(plan, journal, op)
            elif op == "edits.tracks.update":
                results[op] = self._reconcile_simple(plan, journal, op, lambda: _canon(self.client.call(
                    "edits.tracks.get", self._params(plan, track=plan["progress"]["track_body"]["track"])).get(
                    "releases", [])) == _canon(plan["progress"]["track_body"]["releases"]), "track")
            elif op == "edits.tracks.create":
                results[op] = self._reconcile_simple(plan, journal, op, lambda: _exists_track(
                    self.client, self._params(plan), plan["target"]["track_id"]), "track")
            else:
                journal.append(op=op, phase="reconciled", method=e.get("method"), outcome="no effect to settle")
                results[op] = "settled"
        self.out(json.dumps(results, indent=2))
        if "unknown" in results.values():
            raise KitExit(EXIT_UNKNOWN, "still unknown; check Play Console, then decide", results)
        return plan

    def _landed(self, plan):
        tid = plan["target"]["track_id"]
        if plan["command"] == "release":
            vc = int(plan["artifacts"][0]["version_code"])
            return any(vc in [int(a.get("versionCode")) for a in r.get("activeArtifacts", [])]
                       for r in self.client.releases(plan["app"]["package"], tid))
        like = {"plan_id": plan["plan_id"], "app": plan["app"], "principal": plan["principal"]}
        with self.owner().readonly_edit(like) as (eid, _):
            got = self.client.call("edits.tracks.get", {"packageName": plan["app"]["package"], "editId": eid,
                                                        "track": tid}).get("releases", [])
        return _canon(got) == _canon(plan["transition"]["releases_after"])

    def _reconcile_upload(self, plan, journal, op):
        mid = op.split(":")[0]
        kind = op.split(":")[1] if ":" in op else None
        art = plan["artifacts"][0]
        path = art["path"] if not kind else (art["mapping_path"] if kind == "proguard" else art["native_symbols_path"])
        params = self._params(plan) if not kind else self._params(plan, apkVersionCode=art["version_code"],
                                                                   deobfuscationFileType=kind)
        session = self._session(plan, op)
        result, outcome = None, "not_landed"
        try:
            self.owner().resume(plan)
        except KitExit:
            raise
        if session:
            try:
                result = self.client.upload_resumable(mid, params, path, session_url=session)
                outcome = "succeeded"
            except UploadSessionGone:
                result = None
            except UnknownOutcome:
                return "unknown"
        if result is None and not kind:
            for b in self.client.call("edits.bundles.list", self._params(plan)).get("bundles", []):
                if str(b.get("sha256", "")).lower() == art["sha256"].lower():
                    result, outcome = b, "succeeded"
        journal.append(op=op, phase="reconciled", method=mid, edit_id=plan["edit"]["id"], outcome=outcome)
        if outcome == "succeeded":
            plan["progress"]["bundle" if not kind else kind] = self._bind(plan, result) if not kind \
                else play_edit.utcnow(self.now())
        else:
            plan["progress"].pop("bundle" if not kind else kind, None)
            if not kind:
                self.out(f"the bundle did not land; versionCode {art['version_code']} may be consumed — the next "
                         "apply re-checks it")
        plan["state"] = "applying"
        play_edit.save_plan(self.kit.path, plan)
        return outcome

    def _reconcile_simple(self, plan, journal, op, landed, key):
        try:
            self.owner().resume(plan)
            ok = landed()
        except UnknownOutcome:
            return "unknown"
        outcome = "succeeded" if ok else "not_landed"
        journal.append(op=op, phase="reconciled", method=op, edit_id=plan["edit"]["id"], outcome=outcome)
        if ok:
            plan["progress"][key] = play_edit.utcnow(self.now())
        else:
            plan["progress"].pop(key, None)
        plan["state"] = "applying"
        play_edit.save_plan(self.kit.path, plan)
        return outcome

    def cmd_abort(self, force=False):
        plan = play_edit.load_plan(self.kit.path)
        if plan["state"] in ("consumed", "aborted"):
            self.out(f"plan {plan['plan_id']} is already {plan['state']}")
            return plan
        if plan["edit"].get("id"):
            self._need_client()
        self.owner().abort(plan, force=force)
        self.out(f"aborted plan {plan['plan_id']}")
        return plan

    def cmd_bump(self, version_name=None):
        r = build.bump(self.kit.path, version_name)
        if r.get("changed"):
            history_update(self.kit.path, r["version_code"], None, version_name=version_name,
                           bumped=play_edit.utcnow(self.now()))
        self.out(json.dumps(r, indent=2))
        return r


def _in_history(kit, vc, track):
    doc = build.load_yaml(pathlib.Path(kit) / "releases" / "release-history.yaml") or {}
    return any(e.get("version_code") == vc and e.get("track") == track for e in doc.get("releases") or [])


def _exists_track(client, params, tid):
    try:
        client.call("edits.tracks.get", {**params, "track": tid})
        return True
    except PlayError as e:
        if e.status == 404:
            return False
        raise


def load_notes(kit, path) -> list[dict]:
    """releases/notes.yaml: {en-US: "text", de-DE: "…"} → releaseNotes, each ≤ the rules.json limit in code points."""
    p = pathlib.Path(path)
    p = p if p.is_absolute() else pathlib.Path(kit) / p
    doc = build.load_yaml(p)
    if not isinstance(doc, dict) or not doc:
        raise KitExit(EXIT_FINDINGS, f"{p} must map language codes to text")
    limit = rules_mod.value(rules_mod.load(), "release_notes.max_chars")
    out = []
    for lang, text in doc.items():
        text = str(text).strip()
        if len(text) > limit:
            raise KitExit(EXIT_FINDINGS, f"release notes for {lang} are {len(text)} characters; the limit is {limit}")
        out.append({"language": lang, "text": text})
    return out


def render_plan(plan) -> str:
    lines = [f"# Play plan {plan['plan_id']}", "", f"Command: **{plan['command']}** · state **{plan['state']}** · "
             f"publishing **{plan.get('publishing_mode')}**", f"Fingerprint `{plan['fingerprint']}`", "",
             f"Target: `{plan['target']['track_id']}` ({plan['target']['track_role']})",
             f"Console scope sent with the commit: {approvals.scope_text(plan)}", ""]
    for a in plan.get("artifacts") or []:
        lines.append(f"Artifact: `{pathlib.Path(a['path']).name}` {a['package']} {a['version_name']} "
                     f"({a['version_code']}) sha256 `{a['sha256'][:16]}…` signer `{str(a.get('signer_sha256'))[:16]}…`")
    base = plan["remote_baseline"]["tracks"].get(plan["target"]["track_id"], {}).get("releases", [])
    after = (plan.get("transition") or {}).get("releases_after")
    if after is None and plan.get("release"):
        after = merge_release(base, plan["release"])
    lines += ["", "| Release | Before | After |", "|---|---|---|"]
    names = {r.get("name") or ",".join(r.get("versionCodes", [])) for r in base + (after or [])}
    for n in sorted(names):
        b = next((r for r in base if (r.get("name") or ",".join(r.get("versionCodes", []))) == n), None)
        a = next((r for r in after or [] if (r.get("name") or ",".join(r.get("versionCodes", []))) == n), None)
        lines.append(f"| {n} | {_describe(b)} | {_describe(a)} |")
    lines += ["", "## Approvals needed", ""] + [f"- **{g}**: {approvals.effect_sentence(plan, g)}"
                                                 for g in plan["effects"]]
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------------------------------------------ CLI
def build_runner(kit, dry_run=False):
    if dry_run:
        return Runner(kit)
    sa = play_api.resolve_credentials(kit)
    client = play_api.Client(play_api.TokenSource(sa, play_api.urllib_transport))
    return Runner(kit, client=client, sa=sa)


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, *extra):
        p = sub.add_parser(name)
        p.add_argument("kit", nargs="?", default="play-store-kit")
        p.add_argument("--dry-run", action="store_true")
        for e in extra:
            e(p)
        return p

    pend = lambda p: (p.add_argument("--pending", action="append"),  # noqa: E731
                      p.add_argument("--pending-source", choices=("attested", "console"), default="attested"),
                      p.add_argument("--receipt", action="append"))
    vc = lambda p: p.add_argument("--version-code", type=int, required=True)  # noqa: E731
    add("import", lambda p: p.add_argument("--write-kit", action="store_true"))
    add("plan", lambda p: (p.add_argument("--aab", required=True), p.add_argument("--track", required=True),
                           p.add_argument("--status", default="draft"), p.add_argument("--fraction", type=float),
                           p.add_argument("--notes"), p.add_argument("--priority", type=int),
                           p.add_argument("--mapping"), p.add_argument("--native-symbols")), pend)
    add("apply", lambda p: p.add_argument("--receipt", action="append"))
    add("commit", lambda p: p.add_argument("--receipt", action="append"))
    add("status", lambda p: p.add_argument("--track"))
    add("wait", vc, lambda p: (p.add_argument("--track"), p.add_argument("--every", type=int, default=120),
                               p.add_argument("--max-polls", type=int, default=180)))
    add("rollout", vc, pend, lambda p: (p.add_argument("--track", default="production"),
                                        p.add_argument("--fraction", type=float),
                                        p.add_argument("--complete", action="store_true")))
    add("halt", vc, pend, lambda p: p.add_argument("--track", default="production"))
    add("resume", vc, pend, lambda p: (p.add_argument("--track", default="production"),
                                       p.add_argument("--fraction", type=float)))
    add("promote", vc, pend, lambda p: (p.add_argument("--from", dest="source", required=True),
                                        p.add_argument("--to", dest="target", required=True),
                                        p.add_argument("--status", default="draft"),
                                        p.add_argument("--fraction", type=float)))
    add("tracks", pend, lambda p: (p.add_argument("action", choices=("create",)),
                                   p.add_argument("--name", required=True),
                                   p.add_argument("--form-factor", default="default")))
    add("reconcile")
    add("abort", lambda p: p.add_argument("--force", action="store_true"))
    add("bump", lambda p: p.add_argument("--version-name"))
    a = ap.parse_args(argv)
    a.kit = str(pathlib.Path(a.kit).resolve())
    try:
        if a.dry_run and a.cmd in ("import", "apply", "commit", "status", "wait", "reconcile", "abort"):
            print(f"dry run: would {a.cmd} for kit {a.kit}; no network, no auth, no writes")
            return EXIT_OK
        r = runner or build_runner(a.kit, dry_run=a.cmd in ("plan", "bump") and a.dry_run)
        if a.cmd == "import":
            r.cmd_import(a.write_kit)
        elif a.cmd == "plan":
            if a.dry_run:
                r._save_and_ask = lambda plan, gates=None: print(render_plan(plan)) or plan
            r.cmd_plan(a.aab, a.track, a.pending, a.pending_source, a.status, a.fraction, a.notes, a.priority,
                       a.mapping, a.native_symbols)
            if not a.dry_run:
                return EXIT_APPROVAL      # nothing happens until the person approves
        elif a.cmd == "apply":
            r.cmd_apply(a.receipt)
        elif a.cmd == "commit":
            r.cmd_commit(a.receipt)
        elif a.cmd == "status":
            r.cmd_status(a.track)
        elif a.cmd == "wait":
            r.cmd_wait(a.version_code, a.track, a.every, a.max_polls)
        elif a.cmd == "rollout":
            if a.complete == (a.fraction is not None):
                ap.error("give exactly one of --fraction F or --complete")
            r._transition("rollout", a.track, a.version_code, "complete" if a.complete else "fraction", a.pending,
                          a.pending_source, a.receipt, fraction=a.fraction)
        elif a.cmd in ("halt", "resume"):
            r._transition(a.cmd, a.track, a.version_code, a.cmd, a.pending, a.pending_source, a.receipt,
                          fraction=getattr(a, "fraction", None))
        elif a.cmd == "promote":
            r._transition("promote", a.target, a.version_code, "promote", a.pending, a.pending_source, a.receipt,
                          fraction=a.fraction, source=a.source, status=a.status)
        elif a.cmd == "tracks":
            r.cmd_tracks_create(a.name, a.form_factor, a.pending, a.pending_source, a.receipt)
        elif a.cmd == "reconcile":
            r.cmd_reconcile()
        elif a.cmd == "abort":
            r.cmd_abort(a.force)
        elif a.cmd == "bump":
            r.cmd_bump(a.version_name)
        return EXIT_OK
    except KitExit as e:
        print(e.message, file=sys.stderr)
        if e.detail:
            print(json.dumps(e.detail, indent=2, default=str), file=sys.stderr)
        return e.code
    except play_api.ConfigError as e:
        print(e, file=sys.stderr)
        return EXIT_CAPABILITY
    except build.BuildError as e:
        print(e, file=sys.stderr)
        return EXIT_FINDINGS
    except PlayError as e:
        print(play_api.redact(str(e)), file=sys.stderr)
        return EXIT_GONE if e.status == 404 else EXIT_FINDINGS


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
