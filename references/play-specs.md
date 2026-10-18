# Play specifications: text, graphics, sizes

Numbers the kit checks come from `scripts/rules.json` (`python3 scripts/rules.py show <id>`); the rule id is given
where one exists. Everything else here is reference for the person filling in the Console. Read 2026-10-18; check
`.living/` staleness before quoting a number as current.

## Text

| Field | Limit | Rule id | Notes |
|---|---|---|---|
| App name | 30 characters | `listing.title.max_chars` | no emoji, no ALL CAPS unless the brand is, no "#1", "best", "free", prices or Play program names |
| Short description | 80 | `listing.short_description.max_chars` | the biggest benefit; don't repeat it in the full description |
| Full description | 4000 | `listing.full_description.max_chars` | repetitive or irrelevant keywords can lead to suspension |
| Release notes | 500 per language | `release_notes.max_chars` | `releases/notes.yaml` → `releaseNotes[]` |
| Release name | free text via the API | — | the kit uses `versionName (versionCode)`; the 50-character limit sometimes quoted is a tooling convention, not a Play rule |
| Review reply | about 350 | `reviews.reply.max_chars` | v1.1 |
| Tags | 5 | — | Store settings |
| Contact | email required; phone optional; website strongly recommended | — | `edits.details` |

Full-width and half-width characters count the same
(https://support.google.com/googleplay/android-developer/answer/9859152; metadata policy
https://support.google.com/googleplay/android-developer/answer/9898842; best practices
https://support.google.com/googleplay/android-developer/answer/13393723).

## Graphics

| Asset | Spec |
|---|---|
| App icon | 512 × 512, 32-bit PNG with alpha, ≤ 1024 KB, sRGB. Play masks it (corner radius 30 % of the size): don't round the corners yourself; avoid transparency |
| Feature graphic | 1024 × 500, JPEG or 24-bit PNG, no alpha; little or no text |
| Screenshots, all types | JPEG or 24-bit PNG, no alpha; each side 320–3840 px; long side ≤ 2 × short side; at least 2 across device types to publish; up to 8 per device type |
| Phone (featuring bar) | at least 4 at ≥ 1080 px, 16:9 (≥ 1920 × 1080) or 9:16 (≥ 1080 × 1920) |
| 7″ / 10″ tablet, Chromebook | 1080–7680 px, 16:9 or 9:16, at least 4 |
| Wear OS | ≥ 1 of the current version, 1:1, ≥ 384 × 384, no frame, text, background or transparency |
| Android TV | ≥ 1 TV screenshot plus a 1280 × 720 TV banner |
| Automotive | ≥ 2 portrait 800 × 1280 and ≥ 2 landscape 1024 × 768, generic system UI |
| Android XR | 4–8, 8:5, ≤ 8 MB, recommended 3840 × 2400, min 1920 × 1200 |
| Video | one YouTube URL: public or unlisted, embeddable, ads off, not age-restricted, no playlist; only the first 30 s autoplay |

API image types: `phoneScreenshots`, `sevenInchScreenshots`, `tenInchScreenshots`, `tvScreenshots`,
`wearScreenshots`, `icon`, `featureGraphic`, `tvBanner`. There is no type for XR, Chromebook or Automotive, so those
are Console uploads (https://support.google.com/googleplay/android-developer/answer/9866151,
https://developer.android.com/distribute/google-play/resources/icon-design-specifications,
https://developers.google.com/android-publisher/api-ref/rest/v3/AppImageType). Screenshots should show the real app;
localize them when they contain text (graphics fall back to the default language).

## Languages and categories

- 86 store-listing languages, BCP-47-style (`en-US`, `de-DE`, `iw-IL` for Hebrew, `fil`). Untranslated languages may
  show a machine translation; Google sells human translation
  (https://support.google.com/googleplay/android-developer/answer/9844778).
- Categories: 32 app categories (Art and Design … Weather) and 17 game categories; a change takes up to 24 hours
  (https://support.google.com/googleplay/android-developer/answer/9859673).

## Bundle size (compressed download)

| Part | Limit | Rule id |
|---|---|---|
| Base module | 500 MB | `size.base_module.max_mb` |
| Each feature module | 500 MB | `size.feature_module.max_mb` |
| Each asset pack | 1.5 GB | `size.asset_pack.max_mb` |
| All modules + install-time asset packs | 4 GB | `size.install_time_total.max_mb` |
| On-demand / fast-follow packs | 30 GB (34 GB total) | — |
| Mobile-data install warning | above 200 MB | `size.mobile_data_warning_mb` |

`build.py inspect --size` measures with `bundletool get-size total`
(https://support.google.com/googleplay/android-developer/answer/9859372). App Bundles are required for new apps
(since August 2021) and TV apps (since June 2023); `.obb` expansion files are not supported in bundles.

## Version code

Integer, must increase on every upload, never reusable even after a discarded edit, maximum 2,100,000,000
(`version_code.max`; https://developer.android.com/studio/publish/versioning). `versionName` is free text shown to
users.

## Testing tracks

Internal testing up to 100 testers; closed testing lists of up to 2,000 users, 200 lists, 50 per track; open testing
unlimited or capped at ≥ 1,000. Details: `testing-tracks.md`.

## Android vitals bad-behaviour thresholds (last 28 days)

| Metric | Overall | Per phone model | Rule id |
|---|---|---|---|
| User-perceived crash rate | 1.09 % | 8 % | `vitals.crash_rate.bad`, `vitals.per_phone_model.bad` |
| User-perceived ANR rate | 0.47 % | 8 % | `vitals.anr_rate.bad` |

Above a threshold Play can reduce visibility and show a warning on the listing
(https://developer.android.com/topic/performance/vitals).
