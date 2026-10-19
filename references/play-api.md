# Google Play Developer API: what the kit uses and how

The kit talks to `androidpublisher` v3 through `scripts/play_api.py`, a small stdlib client with no Google SDK. Every
method it may call is listed in `CONTRACT` and checked against the pinned discovery document
(`tests/fixtures/discovery/androidpublisher.v3.20261015.json`, `tests/test_contract.py`). A method that is not in the
contract cannot be called.

## Hosts, scope, auth

- API: `https://androidpublisher.googleapis.com/androidpublisher/v3/…`; media uploads under
  `/upload/androidpublisher/v3/…` (resumable: `/resumable/upload/…`).
- Scope `https://www.googleapis.com/auth/androidpublisher`. Vitals use the separate Play Developer Reporting API
  (`https://playdeveloperreporting.googleapis.com`, scope `…/auth/playdeveloperreporting`), which the kit does not
  call in v1.
- **Service-account JSON key only.** The path comes from `KIT/private/credentials.yaml → service_account_json`, else
  `PLAY_SERVICE_ACCOUNT_JSON`. No application-default credentials, no searching the disk. The kit signs an RS256 JWT
  (≤1 h) and exchanges it at `https://oauth2.googleapis.com/token`; tokens are reused until 60 s before expiry. Neither
  the key nor a token is printed, logged or written to a kit file; `validate_kit.py --only secrets` checks.
- Setup and permissions: `play-console-bootstrap.md` §7. No Cloud IAM role grants Play access; access comes only from
  Play Console → Users and permissions, per app.
- Google asks API users not to let third parties publish with their keys and not to publish testing updates more often
  than once a day (https://developers.google.com/android-publisher/api_usage).

## Quotas

"The default quota is 3000 queries per minute for each bucket"; publishing, monetization and review replies share one
bucket. Review reads are limited to 200 GET per hour and 2000 POST per day per app
(https://developers.google.com/android-publisher/quotas, https://developers.google.com/android-publisher/reply-to-reviews).
The kit's traffic is far below these.

## Edits

Insert → change → validate → commit. "Changes made within an edit are not live until the edit is committed", and
after a commit they "can take several hours to take effect". **Each user may have only one open edit**: a new insert
by the same principal silently invalidates the previous one, and a Console save or another user's commit invalidates
every open edit for the app. "You should not use the Developer Console and the Publishing API concurrently", and a
commit "will send all changes for review, including changes ready to send for review in the Developer Console" — hence
`--pending` on every plan (https://developers.google.com/android-publisher/edits,
https://developers.google.com/android-publisher/concurrency-considerations).

`AppEdit {id, expiryTimeSeconds}`: the expiry is absolute; Google does not state the lifetime (rule
`edit.expiry_seconds` is `unverified`), so the kit always reads it from the response. Discarding an edit also removes
bundles uploaded into it. How the kit owns edits: `transactions-and-approvals.md`.

## Methods the kit calls

| Method | Class | Used by |
|---|---|---|
| `edits.insert`, `edits.get`, `edits.validate`, `edits.commit`, `edits.delete` | reconcilable / read / read / reconcilable / destructive | EditOwner |
| `edits.tracks.list`, `edits.tracks.get` | read | import, baseline checks, reconcile |
| `edits.tracks.update` | idempotent | release, transitions |
| `edits.tracks.create` | reconcilable | `tracks create` (type `closedTesting` only) |
| `edits.bundles.upload` (resumable), `edits.bundles.list` | reconcilable / read | apply, reconcile |
| `edits.deobfuscationfiles.upload` (resumable) | reconcilable | apply (`proguard`, `nativeCode`) |
| `edits.listings.list`, `edits.details.get`, `edits.images.list`, `edits.testers.get`, `edits.countryavailability.get` | read | import |
| `edits.listings.get` / `update`, `edits.details.update`, `edits.images.deleteall` | read / idempotent | `listing.py` |
| `edits.images.upload` (resumable, `image/*`) | reconcilable | `listing.py` |
| `edits.testers.update` (Google Groups) | idempotent | contract only; tester sync comes with 1.1 |
| `applications.tracks.releases.list` | read | status, wait, `connect`, landed checks (no edit) |
| `monetization.onetimeproducts.list` / `batchUpdate`, `…purchaseOptions.batchUpdateStates`, `monetization.convertRegionPrices` | read / reconcilable | contract only; product sync comes with 1.2 |

**One-time products use only the monetization API.** The legacy `inappproducts.*` methods answer 403 "Please migrate
to the new publishing API" for a new app, so the kit never calls them. Paths are camelCase (`oneTimeProducts`), except
`onetimeproducts.patch`, whose flatPath is lowercase. Creating a product is three calls: `pricing:convertRegionPrices`
(prices for every region from one base price) → `oneTimeProducts:batchUpdate` with `allowMissing` and
`regionsVersion` → `purchaseOptions:batchUpdateStates` to activate. Every monetization call answers 403 "The caller
does not have permission" until the Google Payments merchant account is set up and verified.

## Store presence (`listing.py`)

Texts come from `KIT/listings.json` (language → Listing), details from `details.json`, graphics from `images.json`
(language → image type → files). `listing.py lint` checks lengths against `rules.json`; `probe` writes each kind into
its own throwaway edit, validates and deletes it, so one refused part does not sink a large change (a 403 at validate
on a draft app means the service account lacks "Edit and delete draft apps"; an empty edit still validates, which is
how the probe tells the cause). `plan` → G-META-PUB → `apply` → `commit` works like a release. Images uploaded into an
edit that is later refused or deleted still appear in the Console's asset library; screenshot order is the upload
order in the edit, so check it in the Console editor after a Console upload.

`edits.commit` always carries `changesInReviewBehavior=ERROR_IF_IN_REVIEW`. Google's default,
`CANCEL_IN_REVIEW_AND_SUBMIT`, cancels a review that is already running; the kit never sends it, and never sends
`changesNotSentForReview` (https://developers.google.com/android-publisher/api-ref/rest/v3/edits/commit).

## Retry classes

| Class | Methods | On 429 | On 5xx / lost response |
|---|---|---|---|
| read | GET, `:validate` | back off (Retry-After), up to 5 times | back off, up to 5 times |
| idempotent | `tracks.update` | resend once | resend once, only if `confirm()` shows the edit is still valid |
| reconcilable | insert, commit, uploads, `tracks.create` | resend once (Google did not process it) | `UnknownOutcome` → journal `unknown` → `reconcile` |
| destructive | `edits.delete` | — | `UnknownOutcome`; the edit expires anyway |

A 401 refreshes the token once. Errors become `PlayError(status, reason, message)`, where the reason is the first
`ErrorInfo.reason`, `errors[].reason` or gRPC status.

## Resumable uploads

Initiate with `X-Upload-Content-Type` / `X-Upload-Content-Length` → `Location` session URL (saved to
`private/sessions/<plan>.json`, redacted from every log) → PUT chunks in multiples of 256 KiB with `Content-Range` →
`308` + `Range` until `200/201` with the resource. After a lost chunk response the client asks the session for its
offset (`Content-Range: bytes */N`) before sending anything; 404/410 means the session is gone
(https://developers.google.com/android-publisher/upload). Google suggests "a timeout of 2 minutes" for bundle uploads.
The returned `Bundle {versionCode, sha1, sha256}` must match the planned artifact or the plan is invalidated.

## Errors the kit names

| Status / reason | Meaning | Kit |
|---|---|---|
| 401 | key revoked, wrong file, clock skew | `connect`: `invalid_key`; exit 8 |
| 403 `SERVICE_DISABLED` / `accessNotConfigured` | API off in that Cloud project | `api_not_enabled` |
| 403 (other) | no permission on this app | `no_play_grant` |
| 403 `apkUpgradeVersionConflict` | versionCode already used | plan invalidated; bump |
| 404 on the app | no first upload yet, or not visible to this account | `not_found` |
| 404 on an edit | expired or superseded | exit 7 |
| 400 `editInvalidated` / 409 | changed in the Console or by another client | exit 7 |
| 400 `FAILED_PRECONDITION` `CHANGES_ALREADY_IN_REVIEW` | review open; edit kept | exit 4 |
| 400 "Only releases with status draft may be created on draft app" | the app was never published | plan `--status draft`; roll out in the Console |
| 403 at `edits.validate` after listing, details or tester changes | draft app, no "Edit and delete draft apps" | grant it; takes a few minutes |
| 403 on `monetization.*` / `pricing:*` | no verified Google Payments merchant account | Monetization setup first |
| 403 "Please migrate to the new publishing API" | legacy `inappproducts` | `monetization.onetimeproducts` |

`PlayError.explain()` prints the likely cause for the last four ("likely cause: …").

## Commands

```
python3 scripts/play_api.py connect  KIT [--track production]   # edit-free capability probe → console-live.json
python3 scripts/play_api.py releases KIT --track production     # releases.list
python3 scripts/play_api.py whoami   KIT                        # client_email only
python3 scripts/play_edit.py show    KIT                        # plan, lock and journal state
python3 scripts/listing.py   lint|probe|plan|apply|commit KIT  # store listing texts, details, graphics
```

## What the API cannot do

Create the app, the first upload, content rating, the App content declarations, category and tags, price and
countries (country availability is read-only), custom store listings and experiments, promotional content,
pre-registration, applying for production access, tester email lists (the API sets Google Groups only), Android XR / Chromebook / Automotive
screenshots, publish / unpublish, managed publishing's **Publish changes**, upload-key reset, Policy status and
appeals. Each is a Console step: `person-and-browser-steps.md`.
