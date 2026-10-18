# Testing tracks and production access

## Tracks

| Track | API id | Testers | Notes |
|---|---|---|---|
| Internal testing | discovered by `release.py import` (Google's page says `qa`; tools commonly use `internal`; never typed by hand) | up to **100**, email lists only | available "within minutes"; may skip standard policy and security review; no country targeting |
| Closed testing | custom names; the default one is `alpha` | email lists (up to 200 lists × 2,000 users, 50 lists per track) or Google Groups | more closed tracks can be created (Console, or `release.py tracks KIT create --name …`, API type `closedTesting`) |
| Open testing | `beta` | anyone with the link; unlimited, or a cap of at least 1,000 | gated by production access for new personal accounts |
| Production | `production` | everyone in the selected countries | staged rollout and halt: `release-and-rollout.md` |

Form-factor tracks carry a prefix: `wear:`, `tv:`, `automotive:`, `android_xr:`, `google_play_games_pc:`, e.g.
`wear:production`. Source: https://developers.google.com/android-publisher/tracks ,
https://support.google.com/googleplay/android-developer/answer/9845334

- **Testers.** The API manages Google Groups only (`edits.testers`); email lists are Console-only. A tester on the
  internal track gets internal builds and must leave that track to receive closed builds. The opt-in link appears once
  the testing release is available.
- **Countries.** Production, open and closed tracks can target countries (synced with production by default); internal
  testing cannot (https://support.google.com/googleplay/android-developer/answer/7550024).
- **Pre-launch report.** Runs on upload or when a production release is saved: stability, compatibility, performance,
  accessibility on Android 9+ phones, tablets, Wear and Chromebooks; results usually within an hour; up to 3 deep
  links; give it a dedicated test account in its settings
  (https://support.google.com/googleplay/android-developer/answer/9842757).
- Whether closed and open testing releases go through policy review before testers see them is not stated precisely
  by Google; plan for a review.

## The closed-test requirement (new personal accounts)

Applies to **personal** accounts created **after 2023-11-13**
(https://support.google.com/googleplay/android-developer/answer/14151465):

- a closed test with at least **12 testers opted in continuously for the last 14 days** at the moment you apply;
  opt-outs reset the count;
- then Dashboard → **Apply for production**: three groups of questions (your closed test, your app, production
  readiness); "review usually takes seven days or less";
- until granted, production, open testing and pre-registration are unavailable.

Google does not address organization accounts on that page; the kit treats them as exempt unless the Dashboard shows an
eligibility task (record it as console evidence).

### Evidence procedure

The API cannot report opt-in history, so the kit counts only what the person reads in the Console:

1. Closed track → Testers tab → note the opted-in count.
2. `python3 scripts/testing_evidence.py add KIT --date 2026-10-01 --testers-opted-in 12 --source console`.
   Repeat every few days; a gap over 7 days between readings is flagged as weak evidence.
3. `testing_evidence.py status KIT` reports the run start (first reading ≥12 after the last one below 12), the earliest
   application day (run start + 14 days) and the age of the latest reading. "Ready to apply" needs a reading on or
   after that day that still shows ≥12.
4. `console_guide.py KIT production-access` drafts the answers from the evidence; the person writes the parts in ‹…›
   and submits in the Console.
5. `testing_evidence.py apply-recorded KIT --date …`, then `decision KIT --date … --outcome granted|denied --reasons …`
   with Google's words quoted. A denial reopens the run; re-apply after addressing the reasons.

`precheck.py` raises W-TESTING-GATE (BLOCKER for production plans only) until a `granted` decision is recorded, and
`status_board.py` shows the countdown.

### Getting real testers

Google looks for genuine use during the test, not just a count: give testers something specific to try, collect
feedback (Play's private feedback on the testing page, a form, a group), ship at least one update during the test, and
mention those changes in the "production readiness" answers. Testers need a Google account on an Android device and
must stay opted in for the whole 14 days.
