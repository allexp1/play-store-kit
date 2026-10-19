#!/usr/bin/env python3
"""Guide the person through the Play Console steps the API cannot do (PLAN-FINAL §8.6, §12).

  python3 scripts/console_guide.py KIT bootstrap|service-account|testing|production-access   # print one section
  python3 scripts/console_guide.py KIT render [--private]       # write KIT/CONSOLE-GUIDE.md (all sections)
  python3 scripts/console_guide.py KIT mark STEP --state todo|attested|observed|skipped [--url URL] [--note TEXT]
  python3 scripts/console_guide.py KIT steps [--json]           # step ids and their progress

Every step names who does it, the values to paste (from facts.yaml / console.yaml) and its Help page. The kit never
signs in, never types legal, identity or payment answers and never presses Create, Save, Publish or Apply. Steps that
grant permissions, accept terms or confirm a release are the person's: the agent may fill a form, the person presses
the button.
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


def console_urls(console: dict) -> dict:
    """Console links built from console.yaml (developer_account_id, console_user_index, app_id); {} when unknown.

    One Google login can hold several developer accounts; /u/N/ picks the login, /developers/ID the account."""
    dev, n, app = console.get("developer_account_id"), console.get("console_user_index"), console.get("app_id")
    if not dev:
        return {}
    base = f"https://play.google.com/console/u/{n if n is not None else 0}/developers/{dev}"
    out = {"home": base, "users": base + "/users-and-permissions"}
    if app:
        out.update(dashboard=f"{base}/app/{app}/app-dashboard", integrity=f"{base}/app/{app}/keymanagement")
    return out


def _open(urls, key):
    return f" Open: {urls[key]}" if key in urls else ""


def load_precheck(kit) -> dict:
    p = pathlib.Path(kit) / "precheck.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def app_access_text(facts: dict) -> str:
    """What to answer on App access, derived from facts (no accounts, a grown-up gate, paid content, sign-in)."""
    d = facts.get("declarations") or {}
    gate = (facts.get("app_access") or {}).get("instructions")
    if d.get("account_creation"):
        return ("**Some functionality is restricted**: add reviewer sign-in for every gated area (kept in "
                "private/app-access.yaml, shown only by `render --private`)"
                + (", including accounts that unlock every AI feature" if d.get("genai") else "")
                + (f". Gate instructions: {gate}" if gate else ""))
    if d.get("digital_goods"):
        return ("**Some functionality is restricted**: Google counts content behind a one-time product as "
                "restricted, so \"all functionality available\" is wrong here. Give the gate instructions"
                + (f" ({gate})" if gate else " (facts.yaml → app_access.instructions)")
                + " and one promo code per product (step `promo-codes`), within 500 characters. Untick \"allow "
                  "Google to use these details for testing on trusted partner devices\" (on by default), or "
                  "partner testing can use up the codes before the reviewer does")
    if gate:
        return f"**Some functionality is restricted**: no sign-in; gate instructions: {gate}"
    if d.get("account_creation") is False:
        return "**All functionality is available without special access**"
    return "❓ (answer declarations.account_creation and digital_goods in facts.yaml)"


def steps(facts: dict, console: dict, pre: dict | None = None) -> list[dict]:
    """The ordered Console steps (§12.1–§12.3) with the values the person needs."""
    pre = pre or {}
    d = facts.get("declarations") or {}
    acct = facts.get("account") or {}
    pkg = facts.get("package") or console.get("package")
    org = acct.get("type") == "organization"
    regulated = [k for k in ("financial", "health", "government", "vpn") if d.get(k)]
    sa = console.get("service_account_email") or "<service-account>@<project>.iam.gserviceaccount.com"
    urls = console_urls(console)
    sells = d.get("digital_goods") or pre.get("billing")
    yes_no = lambda k: {True: "Yes", False: "No"}.get(d.get(k), "❓ (answer declarations." + k + ")")  # noqa: E731
    perms = pre.get("permission_declarations") or []
    signer = pre.get("signer_sha256")
    aab = pre.get("aab")
    out = [
        dict(id="developer-account", section="bootstrap", who="owner", title="Pick the developer account",
             do="If the Console opens on \"Choose developer account\", pick the one that will own the app. Copy "
                "the number after /developers/ into console.yaml → developer_account_id and the N of /u/N/ into "
                "console_user_index; every Console link in this guide is built from them (now: **"
                + (urls.get("home") or "not recorded") + "**).",
             url=HELP + "6112435"),
        dict(id="account", section="bootstrap", who="owner", title="Create the developer account",
             do=f"Account type: **{_v(acct.get('type'))}**"
                + (" (organization: needs a D-U-N-S number)" if org else "")
                + ". 18+, accept the Developer Distribution Agreement, pay the US$25 registration fee, verify "
                  "identity (and a device, for new personal accounts). The Console does not show the account's "
                  "creation date; the testing gate is read from the app's Dashboard instead (step `testing-gate`)."
                + (f" Your declarations ({', '.join(regulated)}) need an organization account." if regulated and not org
                   else ""),
             url=HELP + "6112435"),
        dict(id="verification", section="bootstrap", who="owner", title="Developer verification and package registration",
             do=f"Console Home → follow the verification task; register the package **{pkg}** when asked. Then "
                "`mark verification --state observed --url …` (precheck W-VERIFY reads it).",
             url="https://developer.android.com/developer-verification"),
        dict(id="create-app", section="bootstrap", who="owner", title="Create the app",
             do=f"Home → Create app. App name **{_v(facts.get('app_name'))}**, default language "
                f"**{_v(facts.get('default_locale'))}**, **{_v(facts.get('category_type'))}**, "
                f"**{_v(facts.get('free_or_paid'))}** (a paid app can become free once; a free app can never become "
                f"paid). Package name **{_v(pkg)}**: press Check availability before Create; it can never change. "
                "The form announces Automatic protection (an installer check added to the app; sideloaded copies "
                "ask people to get it from Google Play): set console.yaml → automatic_protection: true if it stays "
                "on. Accept the declarations; you press Create. Then set console.yaml → app_state: draft and "
                "app_id (the number after /app/ in the URL).",
             url=HELP + "9859152"),
        dict(id="testing-gate", section="bootstrap", who="owner", title="Read the testing gate on the Dashboard",
             do="App → Dashboard → Production. If it says to run a closed test with at least 12 testers for at "
                "least 14 days, set console.yaml → testing_gate_shown: true (else false). This is the evidence "
                "precheck uses; the account date is not needed." + _open(urls, "dashboard"),
             url=HELP + "14151465"),
        dict(id="store-listing", section="bootstrap", who="owner", title="Main store listing",
             do="Store presence → Main store listing (search \"Store listing\"): title, short and full description, "
                "icon 512×512, feature graphic 1024×500, phone and tablet screenshots; limits in `play-specs.md`. "
                "`listing.py KIT lint` checks texts first. With the service account's \"Edit and delete draft "
                "apps\" permission, `listing.py` stages it through the API; without it, use the Console editor: "
                "language chip → Manage languages → tick each language by its label → Apply, then Next language "
                "through the translations and Save once. Images an earlier API edit uploaded are already in the "
                "Console's asset library (\"Asset was deduplicated\"); screenshot order is not kept, check it.",
             url=HELP + "9866151"),
        dict(id="store-settings", section="bootstrap", who="owner", title="Store settings",
             do=f"Category **{_v(facts.get('category'))}**, tags, contact email and website.",
             url=HELP + "9859673"),
    ]
    if sells:
        out.append(dict(
            id="payments-profile", section="bootstrap", who="owner", title="Payments profile and merchant account",
            do="Monetize with Play → Monetization setup → set up a Google Payments merchant account and wait for "
               "verification. Until it is verified, products cannot be created and every monetization API call "
               "answers 403; App access, Target audience and Data safety wait on it too (promo codes need products).",
            url=HELP + "7161426"))
    content = ("Privacy policy URL **" + str(_v(facts.get("privacy_policy_url"))) + "**; Ads **" + yes_no("ads")
               + "**; News; Government **" + yes_no("government") + "**; Financial features **"
               + yes_no("financial") + "**; Health **" + yes_no("health") + "**; Advertising ID **"
               + yes_no("advertising_id") + "**."
               + (" Account deletion URL **" + str(_v(facts.get("account_deletion_url"))) + "**."
                  if d.get("account_creation") else ""))
    if perms:
        content += " Permission declarations precheck found: **" + "**, **".join(perms) + "**."
    out += [
        dict(id="app-content", section="bootstrap", who="owner", title="App content",
             do="App content (search \"App content\"): " + content, url=HELP + "9859455"),
        dict(id="app-access", section="bootstrap", who="owner", title="App access (Sign in details)",
             do=app_access_text(facts), url=HELP + "9859455"),
        dict(id="target-audience", section="bootstrap", who="owner", title="Target audience and content",
             do=("Age groups from the app's audience. Children: you tick the COPPA / GDPR certification yourself "
                 "(a legal attestation the agent never makes); the under-13 notice is an acknowledgement. Families "
                 "policy: only Families self-certified ad and analytics SDKs; Teacher Approved is a separate opt-in "
                 "review offered after saving. Saving puts the app in Designed for Families."
                 if d.get("children_audience") else
                 "Age groups; choosing under 13 brings the Families policy." if d.get("children_audience") is False
                 else "❓ (answer declarations.children_audience) Age groups; under 13 brings the Families policy."),
             url=HELP + "9867159"),
        dict(id="data-safety", section="bootstrap", who="owner", title="Data safety form",
             do="Declare what the app and every packaged SDK collect or share (precheck lists the SDKs it found)."
                + (" It can only be saved after Target audience; it then asks whether to show \"Committed to "
                   "follow the Play Families Policy\"." if d.get("children_audience") else ""),
             url=HELP + "10787469"),
        dict(id="content-rating", section="bootstrap", who="owner", title="Content rating",
             do="Fill the IARC questionnaire with an email you read; ratings appear per region.",
             url=HELP + "9859655"),
        dict(id="first-upload", section="bootstrap", who="owner", title="Upload the first signed AAB in the Console",
             do="Test and release → Testing → Internal testing → Create new release. Check the enhancements show "
                "\"Releases are signed by Google Play\" (Play App Signing; nothing to accept any more). Leave "
                "automatic app text translation off when the app ships its own translations. Drag the AAB that "
                "`precheck.py` passed onto the upload area yourself (browser helpers cannot attach files this "
                "large)" + (f": `open -R \"{aab}\"` shows it in Finder" if aab else "") + ". Save. The release "
                "stays a draft: the API cannot work on the app before this upload, and a draft app takes only "
                "draft releases.",
             url=HELP + "9842756"),
        dict(id="upload-cert", section="bootstrap", who="owner", title="Check the upload certificate",
             do="Test and release → App integrity → Play app signing → Upload key certificate → SHA-256."
                + (f" It should read **{_colons(signer)}** (the signer of the AAB precheck read); if it shows "
                   "something else, stop." if signer else "")
                + " Copy it into console.yaml → upload_certificate_sha256 (current: **"
                + str(_v(console.get("upload_certificate_sha256"), "not recorded")) + "**)." + _open(urls, "integrity"),
             url=HELP + "9842756"),
    ]
    if sells:
        out += [
            dict(id="in-app-products", section="bootstrap", who="owner", title="Create the in-app products",
                 do="Monetize with Play → Products → One-time products. Possible only after an AAB with the BILLING "
                    "permission is uploaded (`first-upload`) and the merchant account is verified. Through the "
                    "API only the monetization methods work (pricing:convertRegionPrices → "
                    "oneTimeProducts:batchUpdate → purchaseOptions:batchUpdateStates); product sync arrives in 1.2.",
                 url=HELP + "1153481"),
            dict(id="promo-codes", section="bootstrap", who="owner", title="Promo codes for the reviewer",
                 do="Monetize with Play → Promo codes. The first visit asks you to accept the Promo codes terms "
                    "(your call). One promotion per product (the purchase-option picker takes one): name ≤ 60 "
                    "characters, a start time later than now, an end date, a code count (500 a quarter across "
                    "non-subscription promotions). Each Save downloads promotion_codes (N).csv to Downloads in save "
                    "order: move them to private/ and note which product each is. One code per product goes into "
                    "App access.",
                 url=HELP + "6321495"),
        ]
    out += [
        dict(id="send-for-review", section="bootstrap", who="owner", title="Confirm the release and send for review",
             do="Test and release → the draft release → Edit release → Next (Preview and confirm) → Save. Until "
                "then Publishing overview keeps \"Send app for review\" greyed out. Then Publishing overview → "
                "Send app for review; \"Running quick checks\" takes up to about 14 minutes, then the changes go "
                "to review by themselves. You press these buttons. Afterwards `release.py import KIT --write-kit` "
                "sets app_state to published once a release is live.",
             url=HELP + "9859751"),
        dict(id="cloud-api", section="service-account", who="owner", title="Enable the API in a Google Cloud project",
             do="Google Cloud console → APIs & Services → enable **Google Play Android Developer API** "
                "(and **Google Play Developer Reporting API** only if you want vitals).",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="service-account", section="service-account", who="owner", title="Create a dedicated service account",
             do="IAM → Service accounts → Create (no Cloud roles needed) → Keys → Add key → JSON. Save the file "
                "outside the repository and put its path in private/credentials.yaml → service_account_json. "
                "Never paste the key into chat.",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="invite", section="service-account", who="owner", title="Invite the service account (you do all of it)",
             do=f"Users and permissions → Invite new users → **{sa}** → App permissions → **{pkg}** only. Tick: "
                "Release to testing tracks; Manage testing tracks and edit tester lists; Manage store presence; "
                "Release to production, exclude devices, and use Play App Signing (only if the kit should send "
                "production releases); **Edit and delete draft apps** (Draft apps; needed for listing, details and "
                "tester changes while the app is a draft)"
                + ("; View financial data, and Manage orders and subscriptions, for products" if sells else "")
                + ". You tick the boxes and press Invite: an agent must not grant permissions. Cloud IAM roles do "
                  "not grant Play access. Then check the result with `play_api.py connect KIT`, not by opening "
                  "the form." + _open(urls, "users"),
             url=HELP + "9844686"),
        dict(id="connect", section="service-account", who="kit", title="Check what the key can do",
             do="`python3 scripts/play_api.py connect KIT` writes the capability matrix to console-live.json; then "
                "`release.py import KIT --write-kit` discovers the track ids. A 403 at validate after store-presence "
                "changes means the Draft apps permission is missing; permission changes take a few minutes.",
             url="https://developers.google.com/android-publisher/getting_started"),
        dict(id="closed-track", section="testing", who="owner", title="Create a closed testing track and testers",
             do="Test and release → Testing → Closed testing → Create track (or `release.py tracks KIT create`). Add "
                "testers as a Google Group or an email list; share the opt-in link. Testers on the internal track "
                "do not count and must leave it to receive closed builds. Countries are set only in the Console "
                "(the track → Countries / regions; the API can read them, not set them). A new track starts in "
                "sync with production; tester services often ask for India. You tick the boxes and Save.",
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


def _colons(sha):
    h = str(sha).replace(":", "").upper()
    return ":".join(h[i:i + 2] for i in range(0, len(h), 2))


def all_step_ids() -> set[str]:
    """Every step id, including the ones that appear only for some apps."""
    return {s["id"] for s in steps({"declarations": {"digital_goods": True}}, {})}


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
    ids = all_step_ids()
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


def _steps_for(kit, facts, console):
    return steps(facts, console, load_precheck(kit))


def render_section(kit, name) -> str:
    kit, facts, console = _ctx(kit)
    if name == "production-access":
        return f"## {SECTIONS[name]}\n\n{production_access_draft(kit, facts)}\n"
    prog = load_progress(kit)
    lines = [f"## {SECTIONS[name]}", ""]
    n = 0
    for s in _steps_for(kit, facts, console):
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
                 **(prog.get(s["id"]) or {"state": "todo"})} for s in _steps_for(kit, facts, console)]
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
