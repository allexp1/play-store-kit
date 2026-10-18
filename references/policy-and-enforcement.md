# Policy calendar, enforcement and appeals

Dates move. Values the kit enforces live in `scripts/rules.json` (`python3 scripts/rules.py show`), and
`status_board.py` shows the calendar with 📌 in force, ⚠️ within 60 days and 📅 later. Before quoting any date as
current, check `.living/` staleness and Google's pages: Policy deadlines
https://support.google.com/googleplay/android-developer/answer/9934569 , recent updates
https://support.google.com/googleplay/android-developer/answer/17134731 , timeline
https://developer.android.com/distribute/play-policies . Read 2026-10-18.

## Calendar

| Date | Requirement | Rule id / check | Source |
|---|---|---|---|
| in force since 2026-08-31 | New apps and updates target **API 36** (phones, tablets); Wear and Automotive 35, TV and XR 34. An extension to 2026-11-01 is requested from Policy status, if offered | `target_sdk.*`, B-TARGET | https://support.google.com/googleplay/android-developer/answer/11926878 |
| in force since 2026-08-31 | Existing apps stay visible to new users on newer devices only when targeting ≥ 35 (mobile); Wear 34, TV 33, Automotive 32, XR 34 | `target_sdk.discoverable.mobile.min` | same |
| in force since 2026-08-31 | Play Billing Library **8+** for new apps and updates (extension 2026-11-01) | `billing_library.*`, B-BILLING-LIB | https://developer.android.com/google/play/billing/deprecation-faq |
| 2026-08-01 / 2026-09-15 | 64-bit + 16 KB pages for TV / Wear OS | `page_size.tv_from`, `page_size.wear_from` | https://support.google.com/googleplay/android-developer/answer/17492799 |
| 2026-09-30 | Developer verification: apps registered (most automatically; Console Home shows the rest) | W-VERIFY | https://developer.android.com/developer-verification |
| 2027-01-27 | Contacts Permissions policy (Contact Picker when broad access is not needed); geofencing no longer a foreground-service type; location button recommended for precise location; `READ_CALL_LOG` not allowed for phone-call account verification | R-PERM | answer 9934569 |
| 2027-02-01 | Updates that do not support **16 KB page sizes** cannot be released (phones) | `page_size.update_enforcement`, B-16K | https://developer.android.com/guide/practices/page-sizes |
| from February 2027 | Memory (P90 by RAM tier), bitmap memory and **DEX optimization** (≥25 % obfuscation, optimization, shrinking when DEX > 10 MB apps / 50 MB games) affect visibility | `dex.*`, W-DEX | answer 17492799 |
| from April 2027 | Restore Credentials (zero-tap sign-in restoration) for apps with sign-in | W-RESTORE | answer 17492799 |
| 2027-08-31 | Play Billing Library **9+** (8.x becomes a WARNING now) | `billing_library.next_deadline` | deprecation FAQ |

Recurring rules that are not dated: Data safety for every track except internal, privacy policy on a public
non-PDF URL, account deletion (in app + web link), Financial features and Health apps declarations for every app,
Advertising ID declaration, content rating ("unrated apps are not allowed"), Families policy when children are in the
audience, Play billing for digital goods. Each maps to an R-* check in `precheck-rules.md`.

Google says policies are "generally updated quarterly" and developers "usually get at least 30 days to comply"
(https://play.google/howplayworks/).

## Enforcement actions

| Action | What it means |
|---|---|
| Rejection | the update is refused; the previous version stays live |
| Removal | the app is taken down; no immediate effect on account standing |
| Suspension | removed and counts as a **strike**; repeated strikes can terminate the account |
| Limited visibility / limited regions | still published, shown less or in fewer countries |
| Restricted developer account | limits on what the account can publish |
| Account termination | the account and related accounts are closed |

Notices arrive by email and in Play Console Inbox with the policy, the reason and the appeal route. "Do not attempt
to resubmit a rejected app until you've fixed all the policy violations"
(https://support.google.com/googleplay/android-developer/answer/9899234).

## Policy status and appeals

- App → **Policy status** lists issues and enforcement ("No issues found" when clean), deadlines and extension forms,
  and an **Appeal** button for eligible actions (https://support.google.com/googleplay/android-developer/answer/9842754).
- Appeal troubleshooter: https://support.google.com/googleplay/android-developer/troubleshooter/2993242 ; EEA
  recourse: https://support.google.com/googleplay/android-developer/answer/14659200 .
- The kit reads none of this through the API. The person reads Policy status and records what it says as console
  evidence; `status_board.py` shows "Policy status" as ✋ until it is recorded.

**Writing an appeal** (the person submits it):

1. Quote the notice exactly: policy name, app version, date.
2. If Google is right: what was changed, in which version, with screenshots, and the version that fixes it. Ship the
   fix first.
3. If Google is wrong: the specific feature, why it complies (cite the policy's own words), and how a reviewer can
   see it (account, steps).
4. No threats, no volume, one appeal per issue; GenAI apps can take longer.

## Publish states (Console)

App: Draft · Internal / Closed / Open testing · Pre-registration · Production · No active releases · Unpublished ·
Removed by Google · Suspended by Google. Update: No pending changes · Changes not yet sent for review · In review ·
Update rejected · App rejected. Review takes "up to seven days or longer in exceptional cases", and longer for some
accounts (https://support.google.com/googleplay/android-developer/answer/9859751). Release-level states from the API:
`release-and-rollout.md`.
