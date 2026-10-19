# Bootstrapping an app in Play Console

The steps between "a buildable project" and "the API can work on this app", in order, with who does each.
`python3 scripts/console_guide.py KIT bootstrap` prints them with this app's values; `render` writes
`KIT/CONSOLE-GUIDE.md`; `mark STEP --state attested|observed --url …` records progress. The kit never signs in,
never types legal, identity or payment answers, and never presses Create, Save, Publish or Apply.

## 0. Which developer account (owner)

One Google login can hold several developer accounts. The Console then opens on **Choose developer account**, and
its URLs carry the login index (`/u/4/`) and the account id (`/developers/<id>/`). Record both in `console.yaml`
(`console_user_index`, `developer_account_id`), and the app's id (`/app/<id>/`) as `app_id` once the app exists;
`console_guide.py` builds every Console link from them.

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
   - **Testing requirement** (accounts created after 2023-11-13): see `testing-tracks.md`. The Console does not show
     the account's creation date; read the new app's Dashboard → Production instead and record
     `console.yaml → testing_gate_shown`.

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
| Package name | `package` | press **Check availability** before Create; it can never change afterwards |
| Declarations | person | Developer Program Policies, US export laws |

The form also announces **Automatic protection**: Play adds an installer check to the app's code, and copies
installed from elsewhere ask people to get the app from Google Play (`android-build-and-signing.md`). After Create,
set `console.yaml → app_state: draft` (the app is a draft until its first release is rolled out) and `app_id`.

Menu names since the 2026 refresh: Dashboard · Statistics · Publishing overview · Protected with Play · **Test and
release** (Latest releases and bundles, Production, Testing ▸ Open / Closed / Internal testing, Pre-launch report,
Internal app sharing, Pre-registration, App integrity, Advanced settings) · **Monitor and improve** · **Grow users**
(Store presence) · **Monetize with Play**. Pages move; search a page's name in the Console's search box.

## 3. App content (search "App content")

Each item is a Console form; the person answers it. Values the kit already has are shown by the guide.
Overview: https://support.google.com/googleplay/android-developer/answer/9859455

| Item | What to have ready | Help |
|---|---|---|
| Privacy policy | a public https URL (`privacy_policy_url`) | 9859455 |
| Ads | does the app show ads (any SDK counts) | 9859455 |
| App access (Sign in details) | derived from facts: no accounts and no gate → "All functionality is available without special access"; a grown-up gate → "Some functionality is restricted" with `facts.yaml → app_access.instructions`; sign-in → reviewer accounts (for GenAI apps, accounts that unlock every AI feature), kept in `private/app-access.yaml`, shown only in `render --private`. Content behind a one-time product counts as restricted: give gate instructions plus one promo code per product, within 500 characters, and untick "allow Google to use these details for testing on … trusted partner devices" (on by default) so partner testing does not use up the codes | 9859455 |
| Target audience and content | age groups; under-13 brings the Families policy: you tick the COPPA / GDPR certification yourself (a legal attestation), only Families self-certified ad and analytics SDKs, Teacher Approved is a separate opt-in review. Saving puts the app in Designed for Families | 9867159 |
| News | is it a news app | 9859455 |
| Government, Financial features, Health | the matching `declarations` | 13849271, 14738291 |
| Advertising ID | whether the app reads it (precheck R-ADID) | 6048248 |
| Data safety | collection and sharing per data type, including SDKs; for a children's app it can only be saved after Target audience, and then asks about showing "Committed to follow the Play Families Policy" | 10787469 |
| Permission declarations | every R-PERM form precheck found (for example Foreground service types for `FOREGROUND_SERVICE_MEDIA_PLAYBACK`); the guide lists them from `precheck.json` | 9888170 |
| Content rating | IARC questionnaire; an email address you read | 9859655 |
| Account deletion | a web URL when people can create accounts | 13327111 |

Help base: `https://support.google.com/googleplay/android-developer/answer/<id>`.

**Order for an app with paid content.** App access needs promo codes, promo codes need products, products need a
first upload with the BILLING permission and a verified merchant account; Target audience waits on App access, and
Data safety waits on Target audience. Start the merchant account early.

## 4. Store settings and listing

Category and tags (https://support.google.com/googleplay/android-developer/answer/9859673), contact email
(required) and website. The main store listing (title, short and full description, icon, feature graphic, screenshots)
must be complete before a production release can be reviewed. `listing.py` stages texts, details and graphics
through the API (`lint` → `probe` → `plan` → G-META-PUB → `apply` → `commit`); on a draft app that needs the
service account's "Edit and delete draft apps" permission. Without it, use the Console editor: Default store listing
→ language chip → Manage languages → search each code and tick it by clicking its label (the checkbox moves as the
dialog resizes) → Apply; fill en-US and the graphics, then "Next language" steps through the translations in
alphabetical order by name; Save as draft once at the end. Images an earlier API edit uploaded are already in the
asset library ("Asset was deduplicated"); screenshot order is not kept, so check it in the editor.

## 4a. Payments, products and promo codes (apps that sell digital goods)

1. **Monetize with Play → Monetization setup**: set up the Google Payments merchant account and wait for verification
   (https://support.google.com/googleplay/android-developer/answer/7161426). Until then products cannot be created
   and every monetization API call answers 403.
2. **Products → One-time products**, after the first upload with the BILLING permission
   (https://support.google.com/googleplay/android-developer/answer/1153481). The API route is the monetization API
   only (`play-api.md`); product sync arrives in 1.2.
3. **Promo codes**: the first visit asks to accept the Promo codes terms (the person's call). One promotion per
   product (the purchase-option picker takes one). Name ≤ 60 characters, a start time later than now, an end date, a
   code count (500 a quarter across non-subscription promotions). Each Save downloads `promotion_codes (N).csv` to
   Downloads in save order: move them into `private/` and note which product each belongs to
   (https://support.google.com/googleplay/android-developer/answer/6321495).

## 5. First upload (owner, in the Console)

1. Build a signed release AAB (`build.py bundle`, or your own pipeline) and run `precheck.py` on it until no
   BLOCKER is left.
2. Test and release → Testing → **Internal testing** → Create new release.
3. Check the enhancements line ("2 of 3 enhancements active"): **Releases are signed by Google Play** must be on
   (Play App Signing; there is nothing to accept). Automatic protection is the second; leave **automatic app text
   translation** off when the app ships its own translations.
4. The person drags the AAB onto the upload area (`open -R <aab>` shows it in Finder; browser helpers cannot attach
   files this large). Add release notes, Save.
5. The release stays a **draft** (a draft app takes only draft releases). To send it: Edit release → Next (Preview and
   confirm) → Save, then Publishing overview → Send app for review. "Running quick checks" takes up to about 14
   minutes; the changes then go to review by themselves.

Google requires an artifact uploaded through the Console before the API can edit the app; the kit never tries the API
first (`play_api.py connect` reports `not_found` until this exists).

## 6. Upload certificate (owner reads, kit records)

Test and release → **App integrity** → Play app signing → *Upload key certificate* → SHA-256. The guide prints the
signer SHA-256 precheck read from the AAB: compare, and stop if they differ. Copy it into
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
   for this app only (https://support.google.com/googleplay/android-developer/answer/9844686). **The person does all
   of this step**: ticking permission boxes and pressing Invite grant access, which an agent must not do (Claude
   Code's classifier blocks those clicks too).
   - Release apps to testing tracks
   - Manage testing tracks and edit tester lists
   - Manage store presence
   - **Edit and delete draft apps** (Draft apps section): needed while the app is a draft for every listing,
     details and tester change. Without it those writes upload fine and `edits.validate` answers 403; a draft
     release alone still validates, which hides the cause. It takes a few minutes to apply.
   - Release to production, exclude devices, and use Play App Signing — only if the kit should send production
     releases
   - View financial data, Manage orders and subscriptions — for products (1.2)
   - later: Reply to reviews (1.1)

   Invitations expire after 30 days. Avoid account-level Admin. Check the result with `play_api.py connect KIT`
   rather than by opening the form while the person is on it.
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

The 2026 Console refresh moved pages (for example, account management and "Test and release"; the menu is listed in
§2). When a step's path does not match, search the page name in the Console's search box; the guide names pages, not
menu positions, where it can (https://support.google.com/googleplay/android-developer/answer/18742903).
