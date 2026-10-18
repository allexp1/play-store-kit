#!/usr/bin/env python3
"""Closed-test evidence for production access (PLAN-FINAL §3.5, J6).

  python3 scripts/testing_evidence.py add KIT --date YYYY-MM-DD --testers-opted-in N --source console|attested
                                          [--track ID] [--note TEXT]
  python3 scripts/testing_evidence.py status KIT [--today YYYY-MM-DD] [--json]
  python3 scripts/testing_evidence.py apply-recorded KIT --date YYYY-MM-DD [--note TEXT]
  python3 scripts/testing_evidence.py decision KIT --date YYYY-MM-DD --outcome granted|denied [--reasons TEXT]
                                               [--source console|attested]

Personal developer accounts created after 2023-11-13 need a closed test with at least 12 testers opted in
continuously for the last 14 days before they can apply for production (numbers from scripts/rules.json). The API
cannot report opt-in history, so the person reads the count on the Console's testing page and records it here,
with the date. "Continuously" is computed only from recorded observations: a run starts at the first observation
with enough testers after the last one below the threshold, and it counts as 14 days only when an observation on
or after day 14 still shows enough testers. Internal-testing testers do not count. The kit never applies for
production; the person does, in the Console.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

import build
import play_edit
import rules as rules_mod

FILE = "testing-evidence.yaml"
HEADER = ("# Closed-test evidence for production access. Written by scripts/testing_evidence.py;\n"
          "# each observation is the Opted-in count read from Play Console on that date.\n")
MAX_GAP_DAYS = 7   # a longer gap between observations is shown as weak evidence (Google sees every day; we do not)


def _date(v):
    return v if isinstance(v, dt.date) else dt.date.fromisoformat(str(v))


def load(kit) -> dict:
    doc = build.load_yaml(pathlib.Path(kit) / FILE) or {}
    for k in ("observations", "applications", "decisions"):
        doc.setdefault(k, [])
    return doc


def save(kit, doc):
    import yaml
    for k in ("observations", "applications", "decisions"):
        doc[k] = sorted(doc.get(k) or [], key=lambda e: str(e.get("date")))
    text = HEADER + yaml.safe_dump(json.loads(json.dumps(doc, default=str)), sort_keys=False, allow_unicode=True)
    play_edit.write_atomic(pathlib.Path(kit) / FILE, text)


def requirement(kit) -> dict:
    """Whether the closed-test requirement applies, from facts.yaml."""
    rules = rules_mod.load()
    after = _date(rules_mod.value(rules, "testing_gate.accounts_created_after"))
    acct = (build.load_yaml(pathlib.Path(kit) / "facts.yaml") or {}).get("account") or {}
    if acct.get("type") == "organization":
        applies, why = False, "organization account: not required by default (check the Dashboard for an eligibility task)"
    elif acct.get("type") != "personal":
        applies, why = None, "facts.yaml account.type is not answered"
    elif acct.get("created_after_2023_11_13") is True or (acct.get("created") and _date(acct["created"]) > after):
        applies, why = True, f"personal account created after {after}"
    elif acct.get("created_after_2023_11_13") is False or acct.get("created"):
        applies, why = False, f"personal account created on or before {after}"
    else:
        applies, why = None, "facts.yaml account.created is not answered"
    return {"applies": applies, "why": why, "testers": rules_mod.value(rules, "testing_gate.testers"),
            "days": rules_mod.value(rules, "testing_gate.days")}


def status(kit, today: dt.date) -> dict:
    doc, req = load(kit), requirement(kit)
    need, days = req["testers"], req["days"]
    obs = sorted(doc["observations"], key=lambda o: _date(o["date"]))
    run_start, gaps, prev = None, [], None
    for o in obs:
        d, n = _date(o["date"]), int(o.get("testers_opted_in") or 0)
        if n >= need:
            if run_start is None:
                run_start = d
            elif prev and (d - prev).days > MAX_GAP_DAYS:
                gaps.append(f"{prev} → {d}")
        else:
            run_start, gaps = None, []
        prev = d
    last = obs[-1] if obs else None
    earliest = run_start + dt.timedelta(days=days) if run_start else None
    covered = bool(earliest and last and _date(last["date"]) >= earliest and run_start is not None)
    granted = [d for d in doc["decisions"] if d.get("outcome") == "granted"]
    if granted:
        state = "granted"
    elif req["applies"] is False:
        state = "not_required"
    elif doc["applications"] and not any(_date(d["date"]) >= _date(doc["applications"][-1]["date"])
                                         for d in doc["decisions"]):
        state = "applied_waiting"
    elif covered:
        state = "ready_to_apply"
    elif run_start:
        state = "counting"
    else:
        state = "not_started"
    return {
        "requirement": req, "state": state, "run_start": str(run_start) if run_start else None,
        "earliest_application_day": str(earliest) if earliest else None,
        "days_left": max(0, (earliest - today).days) if earliest else None,
        "last_observation": last and {"date": str(_date(last["date"])), "testers": last.get("testers_opted_in"),
                                      "source": last.get("source")},
        "evidence_age_days": (today - _date(last["date"])).days if last else None,
        "weak_gaps": gaps, "attested_only": bool(obs) and all(o.get("source") != "console" for o in obs),
        "applications": doc["applications"], "decisions": doc["decisions"],
    }


def render(st: dict) -> str:
    req = st["requirement"]
    lines = [f"Closed-test requirement: {'yes' if req['applies'] else 'no' if req['applies'] is False else 'unknown'}"
             f" ({req['why']}); {req['testers']} testers for {req['days']} days"]
    words = {"granted": "✅ production access granted", "not_required": "✅ not required",
             "applied_waiting": "⏸ applied, waiting for Google", "ready_to_apply": "✋ ready: apply in the Console",
             "counting": "✋ counting: keep testers opted in", "not_started": "🔴 no run with enough testers yet"}
    lines.append(words[st["state"]])
    if st["run_start"]:
        left = (f"{st['days_left']} day(s) left" if st["days_left"] else
                "day 14 has passed: record today's Opted-in count to confirm the run")
        lines.append(f"Run with ≥{req['testers']} testers since {st['run_start']}; earliest application "
                     f"{st['earliest_application_day']} ({left})")
    if st["last_observation"]:
        lo = st["last_observation"]
        lines.append(f"Last evidence {lo['date']} ({st['evidence_age_days']} day(s) old): {lo['testers']} opted in, "
                     f"source {lo['source']}")
    if st["weak_gaps"]:
        lines.append(f"Gaps over {MAX_GAP_DAYS} days between observations (record more often): "
                     + ", ".join(st["weak_gaps"]))
    if st["attested_only"]:
        lines.append("All observations are attested; read the Console count to make it console evidence")
    if st["state"] == "ready_to_apply":
        lines.append("Next: Play Console → Dashboard → Apply for production (`console_guide.py KIT production-access` "
                     "drafts the answers), then `testing_evidence.py apply-recorded`")
    return "\n".join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("add")
    a.add_argument("kit")
    a.add_argument("--date", required=True)
    a.add_argument("--testers-opted-in", type=int, required=True)
    a.add_argument("--source", choices=("console", "attested"), required=True)
    a.add_argument("--track")
    a.add_argument("--note", default="")
    s = sub.add_parser("status")
    s.add_argument("kit")
    s.add_argument("--today")
    s.add_argument("--json", action="store_true")
    r = sub.add_parser("apply-recorded")
    r.add_argument("kit")
    r.add_argument("--date", required=True)
    r.add_argument("--note", default="")
    d = sub.add_parser("decision")
    d.add_argument("kit")
    d.add_argument("--date", required=True)
    d.add_argument("--outcome", choices=("granted", "denied"), required=True)
    d.add_argument("--reasons", default="")
    d.add_argument("--source", choices=("console", "attested"), default="console")
    args = ap.parse_args(argv)

    kit = pathlib.Path(args.kit).resolve()
    if args.cmd == "status":
        st = status(kit, _date(args.today) if args.today else dt.date.today())
        print(json.dumps(st, indent=2, default=str) if args.json else render(st))
        return 0
    date = _date(args.date)
    if date > dt.date.today() + dt.timedelta(days=1):
        print(f"refusing a future date {date}", file=sys.stderr)
        return 1
    doc = load(kit)
    if args.cmd == "add":
        if args.testers_opted_in < 0:
            print("testers-opted-in must be zero or more", file=sys.stderr)
            return 1
        entry = {"date": str(date), "testers_opted_in": args.testers_opted_in, "source": args.source}
        if args.track:
            entry["track"] = args.track
        if args.note:
            entry["note"] = args.note
        doc["observations"].append(entry)
    elif args.cmd == "apply-recorded":
        doc["applications"].append({"date": str(date), "note": args.note})
    else:
        doc["decisions"].append({"date": str(date), "outcome": args.outcome, "reasons": args.reasons,
                                 "source": args.source})
    save(kit, doc)
    print(f"recorded {args.cmd} {date}")
    return 0


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
