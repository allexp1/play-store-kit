# Pre-check rules

What `scripts/precheck.py` checks, how it decides, and where each rule comes from. Numbers are read from
`scripts/rules.json` (`python3 scripts/rules.py show`); this page explains them and never overrides them.

## How to read a finding

| Severity | Meaning | Can it be waived? |
|---|---|---|
| BLOCKER | Google will refuse the upload or the review, objectively | never, when the evidence is the artifact itself |
| LIKELY | a rejection is likely | only inference findings, with a reason |
| WARNING | fix soon, or a dated change is coming | yes, with a reason |
| QUESTION | the kit needs an answer in `facts.yaml`; **blocking** questions stop submission because a Console form cannot be filled without the answer | answer it rather than waive it |
| INFO | context | — |

Evidence class, shown on every row: `artifact` (read from the AAB or its merged manifest), `remote` (an API
snapshot), `console` (a Console page observed with a date), `attested` (the person's dated statement),
`inference` (an SDK descriptor or keyword hint in the DEX), `question` (no answer yet), `project` / `facts` (the
repo or `facts.yaml`). Only artifact, remote and console evidence turns a row green.

Waivers go in `KIT/precheck-decisions.yaml`:

```yaml
decisions:
  - rule: R-ADID
    reason: "Ads are contextual; the advertising ID is never read"
    date: 2026-10-18
    decided_by: owner
```

A waiver without all four fields is ignored, and one for an artifact BLOCKER is refused, both visibly.

Extensions granted in Policy status go in `KIT/policy-extensions.yaml` with `source: console`. An extension the
person merely remembers (`attested`) does not lower a BLOCKER.

```yaml
extensions:
  - kind: target_sdk          # or billing_library
    until: 2026-11-01
    observed_at: 2026-10-18
    source: console
```

## Artifact rules

| Id | Check | Severity | Source |
|---|---|---|---|
| A-ARTIFACT | package and versionCode readable; package equals `facts.yaml`; not `debuggable` | BLOCKER | build output |
| B-TARGET | `targetSdk` for new apps and updates since 2026-08-31: phone/tablet 36, Wear OS and Automotive 35, TV and XR 34. Form factors come from `facts.target_devices`, else from required `uses-feature` entries. Before the date, or within a Console-observed extension (to 2026-11-01), a WARNING | BLOCKER | https://support.google.com/googleplay/android-developer/answer/11926878 , https://developer.android.com/google/play/requirements/target-sdk |
| B-64BIT | 32-bit ARM code without arm64-v8a; x86 without x86_64 | BLOCKER | https://support.google.com/googleplay/android-developer/answer/9859372 |
| B-16K | any 64-bit `.so` (arm64-v8a, x86_64) with a `PT_LOAD` segment whose `p_align` is below 16384, read from the ELF program headers. BLOCKER for targetSdk 35+ (every submittable app) or after 2027-02-01; `PAGE_ALIGNMENT_16K` in the bundle config is packaging intent only and never passes the check by itself. 32-bit libraries are listed, not judged | BLOCKER | https://developer.android.com/guide/practices/page-sizes |
| B-SIGN | unsigned; debug certificate; signer SHA-256 not the Console upload certificate (`console.yaml → upload_certificate_sha256`, read from App integrity); a keystore or `keystore.properties` tracked in git. Missing Console certificate → WARNING | BLOCKER | https://support.google.com/googleplay/android-developer/answer/9842756 |
| B-VERSION | `versionCode` above 2,100,000,000 (the maximum itself is allowed); not above the highest code in the remote snapshot or local history. No snapshot → WARNING | BLOCKER | https://developer.android.com/studio/publish/versioning |
| B-SIZE | compressed download (bundletool `get-size total`, `--size`) above 500 MB for the base module → BLOCKER; above 200 MB → WARNING (Play asks phone users about mobile data and installs drop; fix with Play Asset Delivery install-time or fast-follow packs). Without `--size`, a raw AAB above 200 MB → WARNING asking to measure; the raw size never decides a BLOCKER | BLOCKER / WARNING | https://support.google.com/googleplay/android-developer/answer/9859372 |
| N-SYMBOLS | native libraries packaged, no `BUNDLE-METADATA/com.android.tools.build.debugsymbols/` and no `native_symbols_path` (Play warns on upload) | INFO | https://developer.android.com/build/include-native-symbols |
| B-BILLING-LIB | Billing Library major below 8 after 2026-08-31 (unless a Console-observed extension to 2026-11-01) → BLOCKER; 8.x → WARNING for the 2027-08-31 deadline; packaged without a readable version → QUESTION | BLOCKER / WARNING | https://developer.android.com/google/play/billing/deprecation-faq |

Note on B-BILLING-LIB: the plan's catalogue said "below 7 → BLOCKER, below 8 → WARNING". The deprecation FAQ says
version 7's deadline was 2026-08-31 and "by Aug 31, 2026, all new apps and updates to existing apps must use
Billing Library version 8 or later", so the kit blocks below 8 and warns on 8 for the next cycle.

## Policy and declaration rules

| Id | Check | Severity | Source |
|---|---|---|---|
| R-PAY | Stripe / PayPal / Braintree packaged and `digital_goods: true` → LIKELY; unanswered → QUESTION | LIKELY / QUESTION | https://support.google.com/googleplay/android-developer/answer/10281818 |
| R-PERM | SMS / Call Log, all-files access, background location, exact alarms, photo and video, package visibility, install packages, accessibility, VPN, full-screen intent, every foreground-service type → the named Console declaration. READ_CALL_LOG, READ_CONTACTS and FOREGROUND_SERVICE_LOCATION also get the 2027-01-27 change as a WARNING | QUESTION / WARNING | https://support.google.com/googleplay/android-developer/answer/9888170 , https://support.google.com/googleplay/android-developer/answer/17134731 |
| R-ADID | ads or analytics SDK, targetSdk 33+, no `AD_ID` permission → does the app use the advertising ID? | QUESTION | https://support.google.com/googleplay/android-developer/answer/6048248 |
| R-PRIVACY | no `privacy_policy_url`; an account SDK (Firebase Auth, Auth0, Supabase, Cognito) or `account_creation: true` without `account_deletion_url` | QUESTION, blocking | https://support.google.com/googleplay/android-developer/answer/9888076 , https://support.google.com/googleplay/android-developer/answer/13327111 |
| R-DATASAFETY | collecting SDKs packaged → make sure Data safety covers them (WARNING); `collects_nothing: true` in `datasafety.yaml` while one is packaged → LIKELY. On-device-only and end-to-end-encrypted data are out of scope; ephemeral off-device processing must still be declared | WARNING / LIKELY | https://support.google.com/googleplay/android-developer/answer/10787469 |
| R-GENAI | GenAI SDK without a `genai` answer → QUESTION. With `genai: true`: in-app reporting entry point (QUESTION); reviewer accounts unlocking every AI feature in `private/app-access.yaml` (BLOCKER); dated safety tests in `private/genai-safety-tests.md` (LIKELY); ad creatives reviewed (QUESTION); prompts reflected in Data safety and IARC (QUESTION); expect a longer review (INFO) | mixed | https://support.google.com/googleplay/android-developer/answer/13985936 , https://support.google.com/googleplay/android-developer/answer/15748846 , https://android-developers.googleblog.com/2026/08/ensuring-safety-genai-preventing-non-consensual-intimate-content.html |
| R-FAMILIES / R-FINANCE / R-HEALTH / R-GOV / R-UGC | the declaration is unanswered in `facts.yaml` | QUESTION | https://support.google.com/googleplay/android-developer/answer/9859455 |

## Account and quality rules

| Id | Check | Severity | Source |
|---|---|---|---|
| W-ORG | personal account with financial, health, VPN or government declarations | BLOCKER | https://support.google.com/googleplay/android-developer/answer/13634885 |
| W-TESTING-GATE | `console.yaml → testing_gate_shown: true` (the Dashboard asks for the closed test), or, when that is not recorded, a personal account created after 2023-11-13; without a recorded production-access grant production plans are refused (12 testers opted in continuously for 14 days, then apply) | BLOCKER, production only | https://support.google.com/googleplay/android-developer/answer/14151465 |
| W-VERIFY | developer verification / package registration not recorded: `console-live.json` or `console_guide.py mark verification` (observed clears it, attested leaves an INFO) | WARNING | https://developer.android.com/developer-verification |
| I-PROTECT | `console.yaml → automatic_protection: true`: Play adds an installer check; sideloaded copies prompt for Play | INFO | https://support.google.com/googleplay/android-developer/answer/10183279 |
| W-LOCALE | more than one store locale and no `android:localeConfig` | WARNING | https://developer.android.com/guide/topics/resources/app-languages |
| W-DEX | DEX above 10 MB (apps) / 50 MB (games) with no mapping file: from 2027-02-01 Play expects at least 25 % obfuscation, optimization and shrinking | WARNING | https://support.google.com/googleplay/android-developer/answer/17492799 |
| W-RESTORE | sign-in SDK present: Restore Credentials expected from April 2027 | INFO | https://support.google.com/googleplay/android-developer/answer/17492799 |

## What the pre-check cannot see

- The live app's availability cutoffs (existing apps below API 35 on phones stop showing to new users on newer
  devices): that is a property of the published version, shown on the status board, not a submission blocker.
- Runtime 16 KB behaviour: run the app once on a 16 KB emulator image (owner checklist item, recorded as
  `attested` in `releases/release-history.yaml`).
- Policy status, Console declarations and IARC answers: Console-only; the board shows them as 🌐 until observed.
- SDK detection is a DEX string match on known package prefixes. A miss is possible (obfuscated or repackaged
  SDKs); a hit proves the SDK is packaged, not that it runs.
