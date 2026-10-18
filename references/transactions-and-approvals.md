# Transactions and approvals

The contract behind every write the kit makes to Google Play (`release.py`, `play_edit.py`, `approvals.py`). The
rule: **nothing reaches Google without a plan, and nothing in a plan with a live effect runs without the person's own
words, recorded against that exact plan.**

## Plans

`KIT/play-plan.json` (and a readable `play-plan.md`), written by `release.py plan` or a transition run without
`--receipt`:

| Field | What it pins |
|---|---|
| `plan_id`, `command`, `created_at`, `skill_version`, `rules_version` | identity and the rules it was checked with |
| `principal` | the service account (email, key id, fingerprint) |
| `app`, `target` | package, developer account, track id and role, form factor |
| `publishing_mode` | standard or managed, from `console.yaml` |
| `pending_console` | what Publishing overview listed as ready to send, its source (`attested` / `console`) and time |
| `remote_baseline` | the snapshot of the touched tracks from `release.py import` |
| `artifacts` | path, sha256, package, versionCode/name, signer, mapping / symbols |
| `release` / `transition` | the release body, or before → after and the full release list after |
| `mutations` | each API write in order, with its class (idempotent / reconcilable) |
| `effects` | the gates the plan needs |
| `fingerprint` | sha256 over all of the above |

Not fingerprinted: `state`, `edit`, `progress`, `approvals` — they change while the plan runs.

States: `planned → applying → applied → committing → consumed`, or `unknown` (an outcome was lost),
`invalidated` (Google or the Console moved on), `aborted`. Plans are **single-use**: a consumed, invalidated or
aborted plan is never revived, and a new plan is refused while one is `applying`, `applied`, `committing` or
`unknown`. A changed AAB (exit 3), a changed kit file, or a remote track that no longer matches the baseline (exit 7)
means a new plan, never an automatic rebase.

## Gates

| Gate | Asked before | Effect sentence says |
|---|---|---|
| G-UPLOAD | `apply` | which AAB (name, sha256, package, version) enters an edit as a draft on which track, and that the versionCode may be used up |
| G-TEST-DIST | `commit` to a testing track, `promote` to one | who can install what, plus the pending Console scope |
| G-META-PUB | release notes, track creation, a draft commit with pending items | what goes live and under which publishing mode |
| G-PROD-SUBMIT | `commit` / `promote` to production | the release, status and fraction, review, and whether it then goes live by itself (standard) or waits for Publish changes (managed) |
| G-ROLLOUT | `rollout`, `halt`, `resume` | before → after, and which release keeps serving everyone else |

`approvals.py ask KIT --gate G` prints the question verbatim. The person answers in their own words; only then
`approvals.py record KIT --gate G --text "<their words>"` writes `private/approvals/<time>-<gate>.json`
`{gate, plan_id, fingerprint, effects, effect_sentence, scope_quoted, approved_text, approved_at, channel}`.
`--yes` or empty text is not a receipt, and the skill never writes one on its own initiative.

**Checks before the first request** (`approvals.check`): the plan still matches its fingerprint (else exit 3); the
gate is among the plan's effects; the receipt file is inside `private/approvals/`; the gate, plan id and fingerprint
match; the text is not empty. Any failure exits 2, and the test suite asserts that zero requests reached the
transport. One approval covers one gate of one plan; approving the upload does not approve the commit.

## The edit

- One `EditOwner` per plan runs `edits.insert`, `validate`, `commit` and `delete`. Nothing else opens edits.
- The edit id and `expiryTimeSeconds` are saved into the plan **before** any other write, so a crash can resume
  rather than open a second edit (a new insert by the same principal silently supersedes the old one).
- **Lock** `private/locks/<principal>.<package>.lock` holds the plan id, edit id, expiry, host and pid; a second run
  exits 6 instead of fighting over the edit.
- Resuming calls `edits.get`: 404 (expired or superseded) or 400/409 (changed elsewhere) invalidates the plan, exit 7.
- Commits always send `changesInReviewBehavior=ERROR_IF_IN_REVIEW`. A review conflict keeps the edit valid and the plan
  `applied` (exit 4); commit again later with the same receipt. A 400/404/409 commit failure (the Console or another
  client changed the app) invalidates the plan.
- Reads that need an edit (`import`, landed checks) reuse the plan's persisted edit when one is open, otherwise open a
  short-lived edit under the lock and delete it. Status never opens an edit.

## The journal

`private/journal/<plan_id>.jsonl`, append-only, fsynced, secrets redacted. Every write is `started` then
`succeeded` / `failed` / `unknown`; reconciliation appends `reconciled` with an outcome. An operation whose last line
is `started` or `unknown` is **unresolved**, and while any is unresolved the kit refuses to open, abort or retry
(exit 5).

`progress` markers in the plan (`baseline`, `bundle`, `proguard`, `nativeCode`, `track`) let `apply` resume after a
failure without repeating finished steps. Resumable upload session URLs live in `private/sessions/<plan>.json`.

## Reconcile

`release.py reconcile KIT` settles each unresolved operation from what Google reports, never by sending it again blind:

| Lost operation | How it is settled |
|---|---|
| `edits.commit` | `edits.get` still answers → the commit did not land, plan back to `applied`; 404 and the release is on the track (`releases.list`, or a read-only `tracks.get` for transitions) → `consumed`; otherwise still unknown |
| `edits.bundles.upload` | query the saved upload session (it finishes or reports the offset); if the session is gone, look for the sha256 in `edits.bundles.list`; the returned versionCode and sha256 must match the plan |
| `edits.deobfuscationfiles.upload` | query the session; if gone, the step is redone by the next `apply` |
| `edits.tracks.update` / `create` | `tracks.get` in the edit compared with the body that was sent |
| `edits.insert` | at most an orphan edit, superseded by the next insert |

When reconcile cannot decide (exit 5 again), check Play Console and decide with the person; `abort --force` drops the
plan without retrying anything.

## Exit codes

| Code | Meaning | Next step |
|---|---|---|
| 0 | done | |
| 1 | findings (precheck, refused transform, Google rejected the body) | fix and re-plan |
| 2 | approval missing or not matching | ask the question, record the answer, run again with `--receipt` |
| 3 | stale plan or artifact | new plan |
| 4 | review conflict, edit kept | `wait`, then commit again |
| 5 | unknown outcome | `reconcile` |
| 6 | lock held | let the other run finish, or check the lock's pid |
| 7 | edit gone or remote changed | `import`, new plan |
| 8 | capability missing (credentials, API, permission) | `play_api.py connect KIT` |
| 9 | scope or publishing mode unknown | record `--pending` / set `publishing_mode` |
