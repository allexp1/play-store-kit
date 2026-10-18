# Bootstrapping an app in Play Console

The steps between "a buildable project" and "the API can work on this app", in order, with who does each.
`python3 scripts/console_guide.py KIT bootstrap` prints them with this app's values; `render` writes
`KIT/CONSOLE-GUIDE.md`; `mark STEP --state attested|observed --url …` records progress. The kit never signs in,
never types legal, identity or payment answers, and never presses Create, Save, Publish or Apply.

## 1. Developer account (owner)

Six steps, in Google's order (https://support.google.com/googleplay/android-developer/answer/6112435):

1. Sign up with a Google Account; the person must be **18 or older**.
2. Accept the **Developer Distribution Agreement**.
3. Pay the one-time **US$25** fee (MasterCard, Visa, American Express, Discover in the US, Visa Electron outside it;
   prepaid cards are refused).
4. Choose **Personal** or **Organization**. Both reach the same features. An organization account is required for
   financial products, health apps (medical, human-subjects research), apps using `VpnService` and government apps;
   it needs a free **D-U-N-S** number, and the account details must match the Dun & Bradstreet profile
   (https://support.google.com/googleplay/android-developer/answer/13634885). Whether the type can be changed later is
   not stated by Google, so choose deliberately.
5. Verify identity: "you may be asked for a valid government ID and a credit card, both under your legal name"; a
   failed verification does not refund the fee.
6. Personal accounts only:
   - **Device verification** (new accounts since early 2024): Console Home task → scan the QR code with the **Play
     Console mobile app** on a non-rooted Android 10+ phone.
   - **Testing requirement** (accounts created after 2023-11-13): see `testing-tracks.md`.

Personal accounts show the legal name and address from the payments profile on the store page.

**Developer verification and package registration.** Console Home shows a task when an app needs registering;
Play registers most apps automatically, and Play's deadline to register was 2026-09-30
(https://developer.android.com/developer-verification). Record what Home shows with `mark verification`.

## 2. Create the app (owner presses Create)

Home → **Create app** (https://support.google.com/googleplay/android-developer/answer/9859152):

| Field | From | Note |
|---|---|---|
| App name | `facts.yaml → app_name` | max 30 characters; can change later |
| Default language | `default_locale` | |
| App or game | `category_type` | can change later |
| Free or paid | `free_or_paid` | a paid app can be made free; a free app can **never** become paid |
| Declarations | person | Developer Program Policies, US export laws |

The package name is fixed by the **first upload**, not here, and can never change afterwards.

## 3. App content (Policy → App content)

Each item is a Console form; the person answers it. Values the kit already has are shown by the guide.
Overview: https://support.google.com/googleplay/android-developer/answer/9859455

| Item | What to have ready | Help |
|---|---|---|
| Privacy policy | a public https URL (`privacy_policy_url`) | 9859455 |
| Ads | does the app show ads (any SDK counts) | 9859455 |
| App access | reviewer sign-in for every gated area; for GenAI apps, accounts that unlock every AI feature. Kept in `private/app-access.yaml`, shown only in `render --private` | 9859455 |
| Target audience and content | age groups; under-13 brings the Families policy | 9867159 |
| News | is it a news app | 9859455 |
| Government, Financial features, Health | the matching `declarations` | 13849271, 14738291 |
| Advertising ID | whether the app reads it (precheck R-ADID) | 6048248 |
| Data safety | collection and sharing per data type, including SDKs | 10787469 |
| Content rating | IARC questionnaire; an email address you read | 9859655 |
| Account deletion | a web URL when people can create accounts | 13327111 |

Help base: `https://support.google.com/googleplay/android-developer/answer/<id>`.

## 4. Store settings and listing

Category and tags (https://support.google.com/googleplay/android-developer/answer/9859673), contact email
(required) and website. The main store listing (title, short and full description, icon, feature graphic, screenshots)
must be complete before a production release can be reviewed; listing sync through the API arrives in v1.1, so
for now the person fills it in the Console.

## 5. First upload (owner, in the Console)

1. Build a signed release AAB (`build.py bundle`, or your own pipeline) and run `precheck.py` on it until no
   BLOCKER is left.
2. Test and release → Testing → **Internal testing** → Create new release.
3. Accept **Play App Signing** (Google holds the app signing key; you keep the upload key —
   `android-build-and-signing.md`).
4. Upload the AAB, add release notes, save. Sending it to internal testers is optional.

Google requires an artifact uploaded through the Console before the API can edit the app; the kit never tries the API
first (`play_api.py connect` reports `not_found` until this exists).

## 6. Upload certificate (owner reads, kit records)

Test and release → **App integrity** → Play app signing → *Upload key certificate* → SHA-256. Copy it into
`console.yaml → upload_certificate_sha256` and `mark upload-cert --state observed --url <that page>`. From then on
B-SIGN refuses any AAB signed with a different key.

## 7. Service account (J0)

1. Google Cloud console, any project: enable **Google Play Android Developer API** (and the **Google Play Developer
   Reporting API** only for vitals). Linking the developer account to a Cloud project is no longer required
   (https://developers.google.com/android-publisher/getting_started).
2. IAM → Service accounts → Create. No Cloud IAM roles are needed: they do not grant Play access.
3. Keys → Add key → JSON. Store the file outside the repository; put its path in `private/credentials.yaml →
   service_account_json` (or `PLAY_SERVICE_ACCOUNT_JSON`). Never paste the key into chat or commit it.
4. Play Console → **Users and permissions** → Invite new users → the service-account email → **App permissions**
   for this app only (https://support.google.com/googleplay/android-developer/answer/9844686):
   - View app information (read-only)
   - Release apps to testing tracks
   - Manage testing tracks and edit tester lists
   - Release to production, exclude devices, and use Play App Signing — only if the kit should send production
     releases
   - later: Reply to reviews (v1.1); Manage store presence, Manage orders and subscriptions (v1.2)

   Invitations expire after 30 days. Avoid account-level Admin.
5. `python3 scripts/play_api.py connect KIT` → capability matrix in `console-live.json`.

| `connect` says | Meaning | Fix |
|---|---|---|
| `invalid_key` (401) | key revoked, wrong file, clock skew | new key; check the system clock |
| `api_not_enabled` (403 SERVICE_DISABLED) | API off in that Cloud project | enable it (step 1) |
| `no_play_grant` (403) | service account not invited, or no permission on this app | step 4 |
| `not_found` (404) | the app has no first upload, or this account cannot see it | steps 5 and 4 |

6. `python3 scripts/release.py import KIT` discovers the track ids and records the baseline.

Google's guidance on API use: do not let third parties publish on your behalf with your keys, and do not publish
testing updates more often than once a day (https://developers.google.com/android-publisher/api_usage).

## Console navigation note

The 2026 Console refresh moved pages (for example, account management and "Test and release"). When a step's path
does not match, search the page name in the Console's search box; the guide names pages, not menu positions, where
it can (https://support.google.com/googleplay/android-developer/answer/18742903).
