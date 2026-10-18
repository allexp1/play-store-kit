---
last_updated: 2026-10-18
review_interval_days: 7
mode: advisory
spec: living-skills/1.1
---

# Google Play submission: current facts

> Baseline (2026-10-18): SKILL.md, references/ and scripts/rules.json were written
> on this date from the PRIMARY pages in sources.md. From here on this file carries
> what changes after 2026-10-18. Numbers the scripts enforce live in
> scripts/rules.json (edit scripts/rules.src.yaml, then `rules.py freeze`); when
> they change, offer the edit.

## Deadlines watchlist

- 2026-11-01 — end of the target API extension (API 36) and of the Billing Library 7
  extension, for apps granted them in Policy status.
- 2027-01-27 — permission changes: geofencing foreground-service use case, Contacts
  permissions, READ_CALL_LOG for verification.
- 2027-02-01 — updates without 16 KB page support can no longer be released; memory
  P90 tiers and DEX optimization (≥25 % obfuscation, optimization, shrinking above
  10 MB apps / 50 MB games) enforced.
- April 2027 — zero-tap sign-in restoration (Restore Credentials) for apps with
  sign-in on phones and tablets.
- 2027-08-31 — Billing Library 8 deprecation deadline (extension to 2027-11-01).

## Known API gaps (Console or person only)

App creation, content rating (IARC), target audience, ads / app-access declarations,
category, price, countries, custom store listings, experiments, promotional content,
pre-registration, the production-access application after the closed test, developer
verification, "Publish changes" under managed publishing, appeals.

## Contract facts (discovery document revision 20261015)

- Resumable bundle upload: `/resumable/upload/androidpublisher/v3/applications/{packageName}/edits/{editId}/bundles`.
- `edits.commit` defaults to `CANCEL_IN_REVIEW_AND_SUBMIT`; the kit always sends
  `ERROR_IF_IN_REVIEW`.
- `releaseLifecycleState` values carry the `RELEASE_LIFECYCLE_STATE_` prefix
  (DRAFT, NOT_SENT_FOR_REVIEW, IN_REVIEW, APPROVED_NOT_PUBLISHED, NOT_APPROVED, PUBLISHED).

## UNVERIFIED (settle read-only, never assume)

- Internal track id: the guide page prints `qa`, tooling uses `internal`; read it
  from `edits.tracks.list`.
- Edit lifetime: not published; read `expiryTimeSeconds` from each edit.
- Image upload MIME and size limits per image type on the API side.
- Whether closed and open testing releases are reviewed before testers see them.
- Closed-test requirement for organization accounts (Help is silent).
- The exact API error text that asks for `changesNotSentForReview` (capture it on the
  sacrificial app).
- The exact error Google returns for an edit invalidated by a Console change or another
  client's commit, and for a superseded one (the fake uses 400 / 404; capture on the probe).
- Help-page wording of the GenAI reviewer full-access requirement.
- Price ranges per currency.
