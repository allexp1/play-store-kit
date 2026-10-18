#!/usr/bin/env python3
"""Guide the person through the Play Console steps the API cannot do (PLAN-FINAL §8.6, §12).

  python3 scripts/console_guide.py KIT bootstrap|service-account|testing|production-access   # print one section
  python3 scripts/console_guide.py KIT render [--private]       # write KIT/CONSOLE-GUIDE.md (all sections)
  python3 scripts/console_guide.py KIT mark STEP --state todo|attested|observed|skipped [--url URL] [--note TEXT]
  python3 scripts/console_guide.py KIT steps [--json]           # step ids and their progress

Every step names who does it, the values to paste (from facts.yaml / console.yaml) and its Help page. The kit never
signs in, never types legal, identity or payment answers and never presses Create, Save, Publish or Apply.
Progress lives in KIT/console-progress.yaml; only `observed` (with a date and the Console URL it was seen on) turns a
status-board row green, `attested` shows ✋ with the date. `--private` also writes
KIT/private/console-guide.private.html with the reviewer credentials from private/app-access.yaml (never in the .md).
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import pathlib
import sys

import build
import play_edit
import testing_evidence
import validate_kit

HELP = "https://support.google.com/googleplay/android-developer/answer/"
STATES = ("todo", "attested", "observed", "skipped")
MARK = {"todo": "⬜", "attested": "✋", "observed": "✅", "skipped": "➖"}
SECTIONS = {
    "bootstrap": "Bootstrap a new app (J1)",
    "service-account": "Connect a service account (J0)",
    "testing": "Testing track and production access (J6)",
    "production-access": "Apply for production: draft answers",
}


def _v(x, missing="❓ (answer in facts.yaml)"):
    return missing if x in (None, "", []) else x


def steps(facts: dict, console: dict) -> list[dict]:
    """The ordered Console steps (§12.1–§12.3) with the values the person needs."""
    d = facts.get("declarations") or {}
    acct = facts.get("account") or {}
    pkg = facts.get("package") or console.get("package")
    org = acct.get("type") == "organization"
    regulated = [k for k in ("financial", "health", "government", "vpn") if d.get(k)]
    sa = console.get("service_account_email") or "<service-account>@<project>.iam.gserviceaccount.com"
    yes_no = lambda k: {True: "Yes", False: "No"}.get(d.get(k), "❓ (answer declarations." + k + ")")  # noqa: E731
    out = [
        dict(id="account", section="bootstrap", who="owner", title="Create the developer account",
             do=f"Account type: **{_v(acct.get('type'))}**"
                + (" (organization: needs a D-U-N-S number)" if org else "")
                + ". 18+, accept the Developer Distribution Agreement, pay the US$25 registration fee, verify "
                  "identity (and a device, for new personal accounts)."
                + (f" Your declarations ({', '.join(regulated)}) need an organization account." if regulated and not org
                   else ""),
             url=HELP + "6112435"),
        dict(id="verification", section="bootstrap", who="owner", title="Developer verification and package registration",
             do=f"Console Home → follow the verification task; register the package **{pkg}** when asked.",
             url="https://developer.android.com/developer-verification"),
        dict(id="create-app", section="bootstrap", who="owner", title="Create the app",
             do=f"Home → Create app. App name **{_v(facts.get('app_name'))}**, default language "
                f"**{_v(facts.get('default_locale'))}**, **{_v(facts.get('category_type'))}**, "
                f"**{_v(facts.get('free_or_paid'))}** (a paid app can become free once; a free app can never become "
                "paid). Accept the declarations; you press Create.",
             url=HELP + "9859152"),
        dict(id="app-content", section="bootstrap", who="owner", title="App content (Policy → App content)",
             do="Privacy policy URL **" + str(_v(facts.get("privacy_policy_url"))) + "**; Ads **" + yes_no("ads")
                + "**; App access: reviewer sign-in (kept in private/app-access.yaml; `render --private`)"
                + (", including accounts that unlock every AI feature" if d.get("genai") else "")
                + "; Target audience" + (" (children: Families policy applies)" if d.get("children_audience") else "")
                + "; News; Government **" + yes_no("government") + "**; Financial features **" + yes_no("financial")
                + "**; Health **" + yes_no("health") + "**; Advertising ID **" + yes_no("advertising_id")
                + "**; Data safety; Content rating (IARC questionnaire)."
                + (" Account deletion URL **" + str(_v(facts.get("account_deletion_url"))) + "**."
                   if d.get("account_creation") else ""),
             url=HELP + "9859455"),
        dict(id="data-safety", section="bootstrap", who="owner", title="Data safety form",
             do="Declare what the app and every packaged SDK collect or share (precheck lists the SDKs it found).",
             url=HELP + "10787469"),
        dict(id="content-rating", section="bootstrap", who="owner", title="Content rating",
             do="Fill the IARC questionnaire with an email you read; ratings appear per region.",
             url=HELP + "9859655"),
        dict(id="store-settings", section="bootstrap", who="owner", title="Store settings",
             do=f"Category **{_v(facts.get('category'))}**, tags, contact email and website.",
             url=HELP + "9859673"),
        dict(id="first-upload", section="bootstrap", who="owner", title="Upload the first signed AAB in the Console",
             do="Test and release → Testing → Internal testing → Create new release → accept Play App Signing → "
                "upload the AAB that `precheck.py` passed. The API cannot work on an app until this first upload "
                "exists; the kit never tries.",
             url=HELP + "9842756"),
        dict(id="upload-cert", section="bootstrap", who="owner", title="Record the upload certificate",
             do="Test and release → App integrity → Play app signing → Upload key certificate → copy the SHA-256 "
                "into console.yaml → upload_certificate_sha256 (current: **"
                + str(_v(console.get("upload_certificate_sha256"), "not recorded")) + "**).",
             url=HELP + "9842756"),
        dict(id="cloud-api", section="service-account", who="owner", title="Enable the API in a Google Cloud project",
             do="Google Cloud console → APIs & Services → enable **Google Play Android Developer API** "
                "(and **Google Play Developer Reporting API** only if you want vitals).",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="service-account", section="service-account", who="owner", title="Create a dedicated service account",
             do="IAM → Service accounts → Create (no Cloud roles needed) → Keys → Add key → JSON. Save the file "
                "outside the repository and put its path in private/credentials.yaml → service_account_json. "
                "Never paste the key into chat.",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="invite", section="service-account", who="owner", title="Grant app-level Play permissions",
             do=f"Play Console → Users and permissions → Invite new users → **{sa}** → App permissions → **{pkg}** "
                "only: View app information (read-only); Release apps to testing tracks; Manage testing tracks and "
                "edit tester lists; and Release to production, exclude devices, and use Play App Signing only if "
                "the kit should send production releases. Cloud IAM roles do not grant Play access.",
             url=HELP + "9844686"),
        dict(id="connect", section="service-account", who="kit", title="Check what the key can do",
             do="`python3 scripts/play_api.py connect KIT` writes the capability matrix to console-live.json; then "
                "`release.py import KIT` discovers the track ids.",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="closed-track", section="testing", who="owner", title="Create a closed testing track and testers",
             do="Test and release → Testing → Closed testing → Create track (or `release.py tracks KIT create`). Add "
                "testers as a Google Group or an email list; share the opt-in link. Testers on the internal track "
                "do not count and must leave it to receive closed builds.",
             url=HELP + "9845334"),
        dict(id="testing-evidence", section="testing", who="owner", title="Record opted-in testers, dated",
             do="Every few days read the Opted-in count on the closed track's Testers tab and run "
                "`testing_evidence.py add KIT --date … --testers-opted-in N --source console`.",
             url=HELP + "14151465"),
        dict(id="apply-production", section="testing", who="owner", title="Apply for production",
             do="When `testing_evidence.py status` says ready: Dashboard → Apply for production; paste the drafted "
                "answers (production-access section), you press Submit; then `testing_evidence.py apply-recorded`. "
                "Google says review usually takes seven days or less.",
             url=HELP + "14151465"),
    ]
    return out


def production_access_draft(kit, facts) -> str:
    st = testing_evidence.status(kit, dt.date.today())
    lo = st.get("last_observation") or {}
    return "\n".join([
        "Google asks three groups of questions. The kit drafts what it knows; replace every ‹…› with your own "
        "words before submitting.", "",
        "**About your closed test**",
        f"- Testers: {lo.get('testers', '‹count›')} opted in (last read {lo.get('date', '‹date›')}), continuously since "
        f"{st.get('run_start') or '‹date›'}.",
        "- How you recruited testers: ‹friends, a community, a mailing list…›",
        "- How easy it was to recruit testers, and how they used the app: ‹…›",
        "- Feedback you received and how you collected it: ‹…›", "",
        "**About your app**",
        f"- App: {facts.get('app_name') or '‹name›'} ({facts.get('package') or '‹package›'}), "
        f"{facts.get('category_type') or 'app'} in {facts.get('category') or '‹category›'}.",
        "- Intended audience: ‹who it is for›",
        "- What value it gives them: ‹…›",
        "- Expected installs in the first year: ‹…›", "",
        "**Production readiness**",
        "- Changes you made because of the closed test: ‹…›",
        "- How you decided the app is ready for production: ‹…›",
    ])


# ------------------------------------------------------------------------------------------------ progress
def load_progress(kit) -> dict:
    doc = build.load_yaml(pathlib.Path(kit) / "console-progress.yaml") or {}
    return doc.get("steps") or {}


def save_progress(kit, prog):
    import yaml
    text = ("# Console steps progress. Written by scripts/console_guide.py mark.\n"
            + yaml.safe_dump({"steps": prog}, sort_keys=True, allow_unicode=True))
    play_edit.write_atomic(pathlib.Path(kit) / "console-progress.yaml", text)


def mark(kit, step_id, state, url=None, note=None, today=None):
    ids = {s["id"] for s in steps({}, {})}
    if step_id not in ids:
        raise SystemExit(f"unknown step {step_id!r}; one of: {', '.join(sorted(ids))}")
    if state == "observed" and not (url and url.startswith("https://play.google.com/console")):
        raise SystemExit("observed needs --url with the Play Console page it was seen on "
                         "(use --state attested for the person's statement)")
    prog = load_progress(kit)
    entry = {"state": state, "date": str(today or dt.date.today())}
    if url:
        entry["url"] = url
    if note:
        entry["note"] = note
    prog[step_id] = entry
    save_progress(kit, prog)
    return entry


# ------------------------------------------------------------------------------------------------ render
def _ctx(kit):
    kit = pathlib.Path(kit)
    return kit, build.load_yaml(kit / "facts.yaml") or {}, build.load_yaml(kit / "console.yaml") or {}


def render_section(kit, name) -> str:
    kit, facts, console = _ctx(kit)
    if name == "production-access":
        return f"## {SECTIONS[name]}\n\n{production_access_draft(kit, facts)}\n"
    prog = load_progress(kit)
    lines = [f"## {SECTIONS[name]}", ""]
    n = 0
    for s in steps(facts, console):
        if s["section"] != name:
            continue
        n += 1
        p = prog.get(s["id"]) or {"state": "todo"}
        when = f" ({p['state']} {p.get('date')})" if p["state"] != "todo" else ""
        lines += [f"{n}. {MARK[p['state']]} **{s['title']}** · {s['who']}{when} · `{s['id']}`",
                  f"   {s['do']}", f"   Help: {s['url']}", ""]
    return "\n".join(lines)


def render_all(kit) -> str:
    kit, facts, _ = _ctx(kit)
    head = [f"# Play Console guide: {facts.get('app_name') or facts.get('package') or 'app'}", "",
            f"Generated {dt.date.today()} by `console_guide.py render`. Mark progress with "
            "`console_guide.py KIT mark STEP --state attested|observed --url …`.",
            "⬜ to do · ✋ you said it is done · ✅ seen in the Console · ➖ skipped", "", ""]
    return "\n".join(head) + "\n".join(render_section(kit, n) for n in SECTIONS)


def render_private(kit) -> str:
    access = build.load_yaml(pathlib.Path(kit) / "private" / "app-access.yaml") or {}
    rows = "".join(f"<tr><td>{html.escape(str(a.get('label', '')))}</td><td>{html.escape(str(a.get('username', '')))}"
                   f"</td><td>{html.escape(str(a.get('password', '')))}</td><td>{html.escape(str(a.get('notes', '')))}"
                   "</td></tr>" for a in access.get("accounts") or [])
    return ("<!doctype html><meta charset=utf-8><title>App access (private)</title>"
            "<p>Paste into Play Console → App content → App access. Do not share this file.</p>"
            f"<table border=1 cellpadding=6><tr><th>Label</th><th>Username</th><th>Password</th><th>Notes</th></tr>"
            f"{rows or '<tr><td colspan=4>No accounts in private/app-access.yaml</td></tr>'}</table>")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("kit")
    ap.add_argument("cmd", choices=(*SECTIONS, "render", "mark", "steps"))
    ap.add_argument("step", nargs="?")
    ap.add_argument("--state", choices=STATES)
    ap.add_argument("--url")
    ap.add_argument("--note")
    ap.add_argument("--private", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    kit = pathlib.Path(a.kit).resolve()
    if a.cmd in SECTIONS:
        print(render_section(kit, a.cmd))
    elif a.cmd == "mark":
        if not (a.step and a.state):
            ap.error("mark needs STEP and --state")
        print(json.dumps(mark(kit, a.step, a.state, a.url, a.note)))
    elif a.cmd == "steps":
        _, facts, console = _ctx(kit)
        prog = load_progress(kit)
        rows = [{"id": s["id"], "section": s["section"], "who": s["who"], "title": s["title"],
                 **(prog.get(s["id"]) or {"state": "todo"})} for s in steps(facts, console)]
        print(json.dumps(rows, indent=2) if a.json else
              "\n".join(f"{MARK[r['state']]} {r['id']:<18} {r['title']}" for r in rows))
    else:
        text = render_all(kit)
        validate_kit.assert_clean(text, "CONSOLE-GUIDE.md")
        play_edit.write_atomic(kit / "CONSOLE-GUIDE.md", text)
        print(f"wrote {kit / 'CONSOLE-GUIDE.md'}")
        if a.private:
            (kit / "private").mkdir(exist_ok=True)
            play_edit.write_atomic(kit / "private" / "console-guide.private.html", render_private(kit))
            print(f"wrote {kit / 'private' / 'console-guide.private.html'} (gitignored)")
    return 0


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
