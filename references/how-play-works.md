# How Google Play works (context for advice)

Background for answering "how do I get found / featured / paid" questions. None of it is enforced by the kit. Read
2026-10-18; marketing pages are company claims, and the Help Center wins where they disagree.

## Review and safety

- "over 10,000 safety checks on every app" before it becomes available, before each update, and continuously
  (https://play.google/howplayworks/). Google does not say how much is human or automated.
- Review time: "up to seven days or longer in exceptional cases"; longer for some accounts and for GenAI apps
  (https://support.google.com/googleplay/android-developer/answer/9859751).
- Internal testing builds are available "within minutes" and may skip standard review; plan a review for every other
  track.

## Discovery and ranking

Five factors: **user relevance, app quality, editorial value, ads, user experience**; weights vary by surface, device
and the person. Top Charts depend "heavily on popularity"; search depends "mainly on relevance to the query". Google
reads the title, descriptions, category and graphics, identifies traits, and uses ratings, reviews and engagement.
Low-quality apps are kept off discovery surfaces; editors pick Editors' Choice by hand; paid placements are labeled
(https://support.google.com/googleplay/android-developer/answer/9958766).

What that means in practice:

- Listing text and graphics are ranking inputs, not just policy items: a clear title, a benefit-first short
  description, real screenshots for every supported device type.
- Country targeting and device exclusions shrink the audience you can rank in.
- Android vitals above the bad-behaviour threshold (crash 1.09 %, ANR 0.47 %, 8 % per phone model) reduce visibility
  and can add a warning on the listing on affected devices (https://developer.android.com/quality/technical).
- Replying to reviews is associated with a "+0.7 stars" average rating change (Google's guide); the in-app review API
  has an undocumented quota, must never sit behind a button, and its card must not be altered
  (https://developer.android.com/guide/playcore/in-app-review).

## Quality bar

Four pillars: core value, user experience, technical quality, privacy and security; "High-quality apps and games are
rewarded with increased visibility" (https://developer.android.com/quality). Large-screen guidance is now **Adaptive
app quality** tiers (Ready → Optimized → Differentiated); upload tablet and foldable screenshots when you support them.
For featuring, "discovery treatments" need good quality and "featuring treatments" need great quality and strong
marketing assets (https://google.play/business/guides/featuring/).

## Growth tools and who can use them

| Tool | Who | Note |
|---|---|---|
| Custom store listings (up to 50) | everyone | by country, keyword, install state, ads traffic; Console only |
| Store listing experiments | everyone | up to 2 variants, auto-stop after 6 months |
| Pre-registration | everyone | ≤ 90 days per country, ≤ 2 apps at a time |
| Promotional content | **premium growth tools** eligibility (roughly ≥ 1.6 M MAU or ≥ US$40 K/month spend) | a new indie app is not eligible at launch |
| Engage SDK | ≥ 100 K 28-day DAU or Media Experience Program | supported categories only |
| Apps Experience program | ≥ US$1 M group earnings in 12 months | lower fees; not a ranking factor |

Sources: https://support.google.com/googleplay/android-developer/answer/9867158 ,
https://support.google.com/googleplay/android-developer/answer/9859351 ,
https://support.google.com/googleplay/android-developer/answer/9859047 ,
https://google.play/business/guides/premium-growth-tools/ , https://developer.android.com/guide/playcore/engage/eligibility ,
https://google.play/business/programs/appsexperience/ .

## Money

- A service fee applies only to paid apps and in-app digital goods and services sold through Play.
- **2026 fee structure**, rolling out by region: EEA/UK/US 2026-06-30, AU/JP 2026-09-30, KR 2026-12-31, rest of world
  2027-09-30. Where Play Billing processes the purchase there is an added 5 % billing fee. For new installs:
  non-recurring 20 % (10 % on the first US$1 M), recurring 10 %, paid apps 20 %. Until a region switches: 15 % on the
  first US$1 M a year, then 30 %; subscriptions 15 %
  (https://support.google.com/googleplay/android-developer/answer/112622,
  https://support.google.com/googleplay/android-developer/answer/16954621). Rates change; quote the Help page, not
  this table.
- Billing choice program (UK, EEA, AU, JP): alternative billing or external links, always alongside Play Billing,
  transactions reported within 24 h (https://support.google.com/googleplay/android-developer/answer/17161464).
- A free app can never become paid. Prices and countries are Console settings.

## Scale (Google's figures)

"more than 3 billion monthly users across 190+ markets"; Android runs on "over 24,000 device models"
(https://play.google/howplayworks/).
