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
- Secrets the person types into the Console (reviewer passwords) stay in `private/`, shown only by
  `console_guide.py render --private`.

## The steps

| Step | Where | Who | Kit prepares |
|---|---|---|---|
| Developer account (fee, identity, type) | Play Console sign-up | owner | the six steps and which type the declarations need (W-ORG) |
| Device verification | Console Home + Play Console mobile app | owner | the task description |
| Developer verification / package registration | Console Home | owner | W-VERIFY; `mark verification` |
| Create app | Home → Create app | owner | name, language, app/game, free/paid from `facts.yaml` |
| App content declarations | Policy → App content | owner | each answer from `facts.yaml`, precheck R-* findings, Data safety draft |
| Content rating (IARC) | App content → Content rating | owner | which answers the features imply |
| Store settings, listing text and graphics | Grow → Store presence | owner (v1) | limits and lint (`play-specs.md`); API sync arrives in v1.1 |
| First upload | Test and release → Internal testing | owner | signed AAB that passed precheck |
| Upload certificate | App integrity | owner reads | where to copy the SHA-256; B-SIGN enforces it afterwards |
| Service account invite | Users and permissions | owner | exact email and per-app permissions |
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
