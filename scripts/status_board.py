#!/usr/bin/env python3
"""Write KIT/STATUS.md: where the app stands, with the evidence behind every row (PLAN-FINAL §8.8, §3.4).

  python3 scripts/status_board.py KIT [--today YYYY-MM-DD] [--print] [--json]

Reads only local files (no network): console-live.json (capabilities from `play_api.py connect`, tracks from
`release.py import`, lifecycle states from `release.py status`), console.yaml, console-progress.yaml,
testing-evidence.yaml, releases/release-history.yaml, play-plan.json, private/journal/*.jsonl, private/locks/,
scripts/rules.json. Run `release.py status KIT` first for fresh lifecycle states.

✅ done (verified) · ⏸ waiting on Google · 🛑 approval pending · ✋ needs the person · 🌐 Play Console step ·
⬜ not started · 🔴 blocked · ❓ unknown outcome (reconcile). Only artifact, remote and console evidence makes ✅;
attested rows show ✋ with the date; nothing is ✅ because time passed.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

import build
import console_guide
import play_edit
import rules as rules_mod
import testing_evidence
import validate_kit

LIFECYCLE = {   # applications.tracks.releases.list → releaseLifecycleState (prefix stripped)
    "DRAFT": ("⬜", "draft"), "IN_REVIEW": ("⏸", "in review"), "APPROVED_NOT_PUBLISHED": ("✋", "approved, not published"),
    "NOT_APPROVED": ("🔴", "not approved"), "PUBLISHED": ("✅", "published"),
    "NOT_SENT_FOR_REVIEW": ("✋", "saved, not sent for review"), "UNSPECIFIED": ("❓", "state not reported"),
}
STAGES = ("bound", "uploaded", "draft", "submitted", "in_review", "published", "serving")
DEADLINES = [   # (rule id or None, label, date-rule or fixed date, source)
    ("target_sdk.mobile.min", "Target API {v} for new apps and updates (phones)", "target_sdk.mobile.min", None),
    ("target_sdk.extension_until", "Target API extension ends", None, None),
    ("billing_library.extension_until", "Billing Library extension ends", None, None),
    ("page_size.update_enforcement", "16 KB page sizes required for app updates", None, None),
    (None, "Permission policy changes (call log, contacts, location FGS)", "2027-01-27",
     "https://support.google.com/googleplay/android-developer/answer/17134731"),
    ("dex.app.threshold_mb", "Memory / DEX optimization expected (DEX over {v} MB)", "dex.app.threshold_mb", None),
    (None, "Restore Credentials expected for apps with sign-in", "2027-04-01",
     "https://support.google.com/googleplay/android-developer/answer/17492799"),
    ("billing_library.next_deadline", "Billing Library 9 required", None, None),
]


def _json(p):
    p = pathlib.Path(p)
    try:
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    except json.JSONDecodeError:
        return {"_unreadable": str(p)}


def _d(v):
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v)[:10])


def collect(kit, today) -> dict:
    kit = pathlib.Path(kit)
    live = _json(kit / "console-live.json")
    console = build.load_yaml(kit / "console.yaml") or {}
    facts = build.load_yaml(kit / "facts.yaml") or {}
    history = build.load_yaml(kit / "releases" / "release-history.yaml") or {}
    plan = _json(kit / "play-plan.json")
    unresolved = []
    for j in sorted((kit / "private" / "journal").glob("*.jsonl")):
        for e in play_edit.Journal(j).unresolved():
            unresolved.append({"journal": j.name, **e})
    locks = [p.name for p in (kit / "private" / "locks").glob("*.lock")]
    return dict(kit=kit, today=today, live=live, console=console, facts=facts, history=history, plan=plan,
                progress=console_guide.load_progress(kit), testing=testing_evidence.status(kit, today),
                unresolved=unresolved, locks=locks, rules=rules_mod.load())


def journey_rows(c) -> list[tuple]:
    rows = []
    caps = c["live"].get("capabilities") or {}
    if caps.get("publisher_api") == "ok" and caps.get("app_access") == "ok":
        rows.append(("✅", "J0 Connect", f"service account can read the app (remote, {caps.get('checked_at')})"))
    elif caps:
        rows.append(("🔴", "J0 Connect", f"{caps.get('publisher_api')}/{caps.get('app_access', '?')}: "
                                         f"{caps.get('hint', 'see console_guide.py KIT service-account')}"))
    else:
        rows.append(("⬜", "J0 Connect", "run `play_api.py connect KIT`"))
    prog = c["progress"]
    up = prog.get("first-upload", {})
    cert = c["console"].get("upload_certificate_sha256")
    if cert and prog.get("upload-cert", {}).get("state") == "observed":
        rows.append(("✅", "J1 Bootstrap", f"upload certificate recorded (console, {prog['upload-cert'].get('date')})"))
    elif cert or up.get("state") == "attested":
        rows.append(("✋", "J1 Bootstrap", "first upload / certificate attested "
                                         f"{up.get('date') or prog.get('upload-cert', {}).get('date') or ''}; "
                                         "mark it observed from App integrity"))
    else:
        rows.append(("🌐", "J1 Bootstrap", "Console steps: `console_guide.py KIT bootstrap`"))
    imported = c["console"].get("tracks_imported_at")
    rows.append(("✅", "J2 Import", f"track ids discovered (remote, {imported})") if imported
                else ("⬜", "J2 Import", "run `release.py import KIT` after the first upload"))
    st = c["testing"]
    words = {"granted": ("✅", "production access granted"), "not_required": ("✅", "closed-test gate not required"),
             "applied_waiting": ("⏸", "applied for production; waiting for Google"),
             "ready_to_apply": ("✋", "ready to apply for production in the Console"),
             "counting": ("✋", (f"keep testers opted in: {st.get('days_left')} day(s) left" if st.get("days_left")
                                else "day 14 passed: record today's Opted-in count")
                               + f"; earliest {st.get('earliest_application_day')}"),
             "not_started": ("🔴" if st["requirement"]["applies"] else "⬜",
                             f"need {st['requirement']['testers']} opted-in testers on a closed track")}
    icon, text = words[st["state"]]
    if st["requirement"]["applies"] is None and st["state"] not in ("granted",):
        icon, text = "❓", st["requirement"]["why"]
    if st.get("evidence_age_days") is not None and st["state"] in ("counting", "ready_to_apply"):
        text += f" · last evidence {st['evidence_age_days']} day(s) old"
    rows.append((icon, "J6 Testing gate", text))
    mode = c["console"].get("publishing_mode") or "unknown"
    rows.append(("🌐" if mode == "unknown" else "✅", "Publishing mode",
                 mode if mode != "unknown" else "read Publishing overview in the Console"))
    rows.append(("🌐", "Policy status", "Console only: Policy → Policy status"))
    return rows


def release_rows(c) -> list[dict]:
    remote = {}
    for track, t in (c["live"].get("releases_status") or {}).items():
        for r in t.get("releases", []):
            state = str(r.get("releaseLifecycleState", "")).replace("RELEASE_LIFECYCLE_STATE_", "")
            for vc in r.get("versionCodes", []):
                remote[(str(vc), track)] = (state, t.get("checked_at"))
    out = []
    for h in (c["history"].get("releases") or [])[-10:]:
        vc, track = str(h.get("version_code")), h.get("track")
        stages = h.get("stages") or {}
        state, checked = remote.get((vc, track), (None, None))
        icon, word = LIFECYCLE.get(state, ("⬜", "no remote state yet")) if state else ("⬜", "run release.py status")
        if state == "PUBLISHED" and not stages.get("serving"):
            icon, word = "⏸", "published; serving not observed yet"
        out.append({"version_code": vc, "version_name": h.get("version_name"), "track": track, "icon": icon,
                    "state": word, "checked_at": checked,
                    "stages": " · ".join(f"{s} {str(stages[s])[:10]}" for s in STAGES if stages.get(s)) or "—"})
    return out


def deadline_rows(c) -> list[tuple]:
    rules, today, out = c["rules"], c["today"], []
    for rid, label, ref, src in DEADLINES:
        if rid is None:
            date, url = _d(ref), src
        else:
            r = rules.get(rid) or {}
            if ref:
                date = r.get("effective_from")
                label = label.format(v=r.get("value"))
            else:
                date = r.get("value")
            url = r.get("source_url")
        if not date:
            continue
        date = _d(date)
        days = (date - today).days
        icon = "📌" if days < 0 else "⚠️" if days <= 60 else "📅"
        out.append((icon, str(date), label, "in force" if days < 0 else f"in {days} day(s)", url))
    return sorted(out, key=lambda r: r[1])


def render(c) -> str:
    facts, plan = c["facts"], c["plan"]
    lines = [f"# Play status: {facts.get('app_name') or facts.get('package') or 'app'}", "",
             f"Generated {c['today']} by `status_board.py` from local files; run `release.py status KIT` first for "
             "fresh review states.", "",
             "✅ verified · ⏸ waiting on Google · 🛑 approval pending · ✋ needs you · 🌐 Console step · ⬜ not started · "
             "🔴 blocked · ❓ reconcile", "", "## Journeys", "", "| | Item | Evidence |", "|---|---|---|"]
    lines += [f"| {i} | {n} | {t} |" for i, n, t in journey_rows(c)]
    if c["unresolved"]:
        lines += ["", "## ❓ Needs reconcile", "",
                  "These operations may or may not have happened at Google. Run `release.py reconcile KIT` before "
                  "anything else; nothing is retried blind.", ""]
        lines += [f"- ❓ {e['op']} ({e.get('method')}) {e['phase']} at {e.get('ts')} · {e['journal']}"
                  for e in c["unresolved"]]
    if plan and "_unreadable" not in plan:
        state = plan.get("state")
        icon = {"planned": "🛑", "approved": "🛑", "applied": "🛑", "unknown": "❓", "invalidated": "🔴",
                "committed": "⏸", "consumed": "✅", "aborted": "➖"}.get(state, "⬜")
        edit = (plan.get("edit") or {}).get("id")
        lines += ["", "## Open plan", "", f"{icon} `{plan.get('plan_id')}` {plan.get('command', '')}: **{state}**"
                  + (f" · edit persisted (expires {plan['edit'].get('expiry_time_seconds')})" if edit else "")
                  + (f" · lock held ({', '.join(c['locks'])})" if c["locks"] else "")]
    rel = release_rows(c)
    lines += ["", "## Releases", ""]
    if rel:
        lines += ["| | versionCode | Name | Track | Google | Stages |", "|---|---|---|---|---|---|"]
        lines += [f"| {r['icon']} | {r['version_code']} | {r['version_name'] or ''} | {r['track']} | {r['state']}"
                  + (f" ({r['checked_at']})" if r["checked_at"] else "") + f" | {r['stages']} |" for r in rel]
    else:
        lines.append("⬜ no releases planned yet")
    lines += ["", "## Deadlines", "", "| | Date | What | When | Source |", "|---|---|---|---|---|"]
    lines += [f"| {i} | {d} | {w} | {n} | {u or ''} |" for i, d, w, n, u in deadline_rows(c)]
    return "\n".join(lines) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit", nargs="?", default="play-store-kit")
    ap.add_argument("--today")
    ap.add_argument("--print", action="store_true", help="print instead of writing STATUS.md")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    kit = pathlib.Path(a.kit)
    c = collect(kit, _d(a.today) if a.today else dt.date.today())
    if a.json:
        print(json.dumps({"journeys": journey_rows(c), "releases": release_rows(c), "deadlines": deadline_rows(c),
                          "unresolved": c["unresolved"], "plan_state": c["plan"].get("state")}, indent=2, default=str))
        return 0
    text = render(c)
    validate_kit.assert_clean(text, "STATUS.md")
    if a.print:
        print(text)
    else:
        play_edit.write_atomic(kit / "STATUS.md", text)
        print(f"wrote {kit / 'STATUS.md'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
