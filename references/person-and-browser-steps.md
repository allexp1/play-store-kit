# Steps only a person can do (and how the kit helps)

Some Play work has no API, and some must not be automated even where a browser could reach it. This page lists each
step, who does it, and what the kit prepares. `console_guide.py` prints the steps with this app's values;
`console_guide.py KIT mark STEP --state attested|observed --url …` records progress, and `status_board.py` shows it.

## Hard rules

- The kit **never signs in** to Play Console or a Google account, never types passwords, identity, payment or legal
  answers, and never presses Create, Save, Send for review, Publish, Apply, Appeal or Delete.
- A browser helper may **read** a Console page the person has open, to record what it shows (`--state observed` with
  the page URL); it never clicks a button that changes the account. Before reading, it says which page it will read.
- Every live effect through the API goes through a plan and the person's own approval
  (`transactions-and-approvals.md`).
- Secrets the person types into the Console (reviewer passwords, promo codes) stay in `private/`, shown only by
  `console_guide.py render --private`.
- **The person presses the buttons that change the account.** An agent with a browser may fill text into a form the
  person opened (listing texts, release notes), and stops before the button. Granting permissions (Invite user, its
  permission boxes), accepting terms (Promo codes), legal attestations (COPPA / GDPR on Target audience), "select all"
  on Countries / regions, Save on any form, Edit release → Next → Save, Send app for review and every
  `edits.commit` are the person's: Claude Code's safety classifier blocks those clicks, and they are the owner's to
  make anyway. For a commit the person runs `! .venv/bin/python scripts/release.py commit KIT --receipt …` (or
  `listing.py commit`) in the session. After the person saves, the kit checks the result through the API or by
  reading the page.
- **Files the person drags in.** A browser helper cannot attach a real AAB (Claude in Chrome's file upload is capped
  at 10 MB per call). `open -R <aab>` shows the file in Finder; the person drags it onto the release's upload area.

## The steps

| Step | Where | Who | Kit prepares |
|---|---|---|---|
| Pick the developer account | "Choose developer account" | owner | `developer_account_id` and `console_user_index` (the `/u/N/`) in console.yaml; Console links are built from them |
| Developer account (fee, identity, type) | Play Console sign-up | owner | the six steps and which type the declarations need (W-ORG) |
| Device verification | Console Home + Play Console mobile app | owner | the task description |
| Developer verification / package registration | Console Home | owner | W-VERIFY; `mark verification` |
| Create app | Home → Create app | owner | name, language, app/game, free/paid and package name (Check availability) from `facts.yaml` |
| Testing gate | App → Dashboard → Production | owner reads | `testing_gate_shown` in console.yaml |
| Payments profile, merchant account | Monetize with Play → Monetization setup | owner | needed before any product (digital goods) |
| In-app products, promo codes | Monetize with Play → Products / Promo codes | owner | after the first upload with BILLING; one promotion per product |
| App content declarations | Policy → App content | owner | each answer from `facts.yaml`, precheck R-* findings, Data safety draft |
| Content rating (IARC) | App content → Content rating | owner | which answers the features imply |
| Store settings, listing text and graphics | Store presence (Grow users) | owner, or `listing.py` | `listing.py lint/probe/plan`; on a draft app the API needs "Edit and delete draft apps", else the Console editor |
| First upload | Test and release → Testing → Internal testing | owner drags the AAB | signed AAB that passed precheck; `open -R` path |
| Confirm the draft release, Send for review | the release → Edit release; Publishing overview | owner | the board says when it is waiting |
| Upload certificate | App integrity | owner reads | where to copy the SHA-256; B-SIGN enforces it afterwards |
| Service account invite | Users and permissions | owner, all of it | exact email and the boxes to tick; checked afterwards with `play_api.py connect` |
| Tester email lists, opt-in link | Testing → Testers | owner | `testing-tracks.md` |
| Closed-test evidence | Closed track → Testers | owner reads | `testing_evidence.py add` dated readings |
| Apply for production | Dashboard | owner | `console_guide.py KIT production-access` draft with ‹…› gaps |
| Send changes for review / Publish changes | Publishing overview | owner | the board says when something is waiting |
| Price, countries, custom listings, experiments, pre-registration | Console | owner | reference only (`how-play-works.md`) |
| Policy status, appeals | App → Policy status | owner | `policy-and-enforcement.md` appeal outline |
| Upload-key reset | App integrity | owner | `android-build-and-signing.md` |

## Recording what the Console shows

```
python3 scripts/console_guide.py KIT mark upload-cert --state observed \
    --url https://play.google.com/console/u/0/developers/<id>/app/<app>/keymanagement --note "SHA-256 copied"
python3 scripts/console_guide.py KIT mark content-rating --state attested --note "owner says IARC done 2026-10-18"
```

`observed` needs a `play.google.com/console` URL and counts as console evidence (✅ on the board); `attested` is the
person's word (✋). The 2026 Console refresh moved pages, so find a page by its name in the Console's search box rather
than by menu position (https://support.google.com/googleplay/android-developer/answer/18742903).
