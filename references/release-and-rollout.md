# Releases, review and staged rollout

How `release.py` moves a build from an AAB on disk to users, and what Google does in between. The transaction rules
behind every write (plans, receipts, lock, journal, reconcile) are in `transactions-and-approvals.md`.

## The objects

- **Track** `{track, releases[]}`. Ids come from `release.py import` (`production`, `beta`, the internal id, closed
  names, form-factor prefixes like `wear:production`); see `testing-tracks.md`.
- **Release** `{name, versionCodes[], status, userFraction, releaseNotes[{language, text}], inAppUpdatePriority,
  countryTargeting}`. `status` is `draft | inProgress | halted | completed`; `userFraction` is strictly between 0 and
  1 and only with `inProgress`/`halted`; `inAppUpdatePriority` is 0–5 (default 0); `countryTargeting` only on
  `inProgress` production releases (the kit does not set it). A track update replaces the whole release list, so the
  kit always sends the current list with one change
  (https://developers.google.com/android-publisher/api-ref/rest/v3/edits.tracks).
- **Release status and review state are separate.** `status` is what you asked for; Google's progress is
  `releaseLifecycleState` from `applications.tracks.releases.list`, which needs no edit:

| `releaseLifecycleState` (prefix `RELEASE_LIFECYCLE_STATE_`) | Board | Meaning |
|---|---|---|
| `DRAFT` | ⬜ | saved as a draft |
| `NOT_SENT_FOR_REVIEW` | ✋ | saved, waiting for someone to send it for review in the Console |
| `IN_REVIEW` | ⏸ | Google is reviewing |
| `APPROVED_NOT_PUBLISHED` | ✋ | approved; managed publishing waits for **Publish changes** |
| `NOT_APPROVED` | 🔴 | rejected; read the Policy status / Inbox in the Console |
| `PUBLISHED` | ✅ | published (serving is not observed by the API) |
| `UNSPECIFIED` | ❓ | not reported |

## A release, step by step (J3)

```
release.py import KIT --write-kit                       # once per app, and before every plan that follows Console work
release.py plan   KIT --aab app.aab --track internal --pending none [--status completed] [--notes releases/notes.yaml]
  → exit 2 + the G-UPLOAD (and G-TEST-DIST / G-PROD-SUBMIT / G-META-PUB) questions; nothing sent
approvals.py record KIT --gate G-UPLOAD --text "<the person's words>"
release.py apply  KIT --receipt private/approvals/…-G-UPLOAD.json
  → edit opened, baseline compared, bundle uploaded (resumable), sha256 + versionCode checked,
    mapping / native symbols uploaded, track updated, edit validated — not committed
approvals.py record KIT --gate G-TEST-DIST --text "…"
release.py commit KIT --receipt private/approvals/…-G-TEST-DIST.json
release.py status KIT    /    release.py wait KIT --version-code 42
```

- `--status draft` (default) only needs G-UPLOAD: the release lands as a draft that a person finishes in the Console.
  `completed` or `inProgress` adds G-TEST-DIST on testing tracks or G-PROD-SUBMIT on production.
- Release notes: `releases/notes.yaml` maps language codes to text, at most 500 characters each (rule
  `release_notes.max_chars`). Notes add G-META-PUB, because under managed publishing they are not held.
- `--pending` is what Publishing overview → *Changes ready to send for review* lists right now. A commit sends those
  changes as well, and the question quotes them. `none` means the list is empty.
- The plan refuses: a package that differs from the kit, a BLOCKER from `precheck.py` (W-TESTING-GATE counts only
  for production), a versionCode not above every code in the snapshot, `inProgress` without a fraction, and a staged
  first production release.
- Google answers a reused versionCode with `apkUpgradeVersionConflict`; the plan is invalidated and you bump
  (`release.py bump KIT`, then rebuild). A versionCode uploaded into an edit that is later discarded may stay used.
- Debug symbols: `--mapping` (R8/ProGuard `mapping.txt`) uploads as `proguard`, `--native-symbols` (zip) as
  `nativeCode` (`android-build-and-signing.md`).
- Google asks API users not to publish testing updates more than once a day
  (https://developers.google.com/android-publisher/api_usage).

## Review and publishing

- Every commit uses `changesInReviewBehavior=ERROR_IF_IN_REVIEW`. If something is already in review, Google refuses
  the commit with `CHANGES_ALREADY_IN_REVIEW` and the edit stays valid: exit 4, wait (`release.py wait`), commit again
  with the same receipt. The kit never sends Google's default, which silently cancels the open review
  (https://developers.google.com/android-publisher/api-ref/rest/v3/edits/commit).
- **Standard publishing**: an approved release goes live by itself. **Managed publishing**: approved changes wait
  until someone presses **Publish changes** in the Console ("within a few minutes" after that). Managed publishing does
  **not** hold a staged rollout going to 100 %, release notes, device exclusions or unpublishing
  (https://support.google.com/googleplay/android-developer/answer/9859654). The kit never presses Publish.
- Review "can take a few hours or up to seven days (or longer in exceptional cases)"; plan a buffer of a week before
  any date that matters (same page).
- `publishing_mode` in `console.yaml` must say `standard` or `managed` before any plan (exit 9 otherwise). Read it
  from Publishing overview.

## Staged rollout (J5)

Staged rollouts are for updates only, never the first production release; the percentage "won't increase
automatically"; halting stops new users from getting the release; a fix ships as a new release
(https://support.google.com/googleplay/android-developer/answer/6346149, https://developers.google.com/android-publisher/tracks).

| Command | Before → after | Kit rules |
|---|---|---|
| `rollout --version-code N --fraction F` | inProgress f → inProgress F | F only goes up, 0 < F < 1 |
| `rollout --version-code N --complete` | inProgress → completed | the previous completed release is dropped |
| `halt --version-code N` | inProgress → halted | needs another completed release serving everyone; a completed release is never halted |
| `resume --version-code N [--fraction F]` | halted → inProgress | fraction kept or raised |
| `promote --from qa --version-code N --to production [--status inProgress --fraction F]` | copy name, codes, notes | G-PROD-SUBMIT to production, G-TEST-DIST to a testing track |

Each transition is one plan, two runs: without `--receipt` it saves the plan and prints the G-ROLLOUT question
(exit 2, nothing sent); with the receipt it opens an edit, checks the track still matches the snapshot, applies
exactly the planned release list, validates and commits. Run `release.py import KIT` first: after any commit (yours,
the Console's or another tool's) the snapshot is old and the transition stops with exit 7 instead of rebasing.

The kit deliberately does not offer: lowering a rollout fraction, halting the serving release, country targeting,
`changesNotSentForReview`. Do those in the Console.

## New closed tracks

`release.py tracks KIT create --name friends [--form-factor wear]` creates a `closedTesting` track (G-META-PUB).
Testers are added in the Console (email lists) or as Google Groups; see `testing-tracks.md`.

## Status and history

- `release.py status KIT` reads `applications.tracks.releases.list` for every bound track (no edit, so it never
  disturbs an open plan), writes `console-live.json → releases_status`, and records `in_review` / `published` in
  `releases/release-history.yaml`. The list holds at most 20 releases per track.
- `release.py wait KIT --version-code N` polls (default every 120 s, 180 polls) until `APPROVED_NOT_PUBLISHED`,
  `NOT_APPROVED` or `PUBLISHED`, printing each change. It never writes.
- `status_board.py KIT` turns those files into `STATUS.md`.
- History stages: `bound` (plan) → `uploaded` → `draft` (applied) → `submitted` (committed) → `in_review` →
  `published`; `serving` is attested by a person who saw it on a device.
