# Changelog

## 1.0.2 (2026-10-19)

The rest of the first real run (Kids Love Science): what the live Console and API showed.

- `listing.py`: store listing texts, app details and graphics through the API. `lint` (limits from rules.json), `probe` (each kind validated in its own throwaway edit, so a missing permission shows before a big change), `plan` → G-META-PUB → `apply` → `commit`, with a baseline check against Console changes.
- API contract: listings get/update, details update, images deleteall/upload, testers update, and the monetization one-time product methods (`oneTimeProducts`, `convertRegionPrices`, `purchaseOptions:batchUpdateStates`), each checked against the discovery document. The legacy `inappproducts` API is never called.
- `PlayError.explain()` names the cause of the refusals seen live: no merchant account (403 on monetization), missing "Edit and delete draft apps" (403 at validate), the legacy products API, and a non-draft release on a draft app.
- Draft apps: `console.yaml app_state: draft` makes `release.py plan` refuse a non-draft status before any request; `import --write-kit` switches it to published once a release is live. FakePlay models draft apps and the draft-apps permission.
- `import` says "read from Play" for a fresh read, and `--write-kit` updates only `tracks` and `tracks_imported_at` in console.yaml, keeping the person's comments and other keys.
- Pre-check: a raw AAB over the 200 MB mobile-data point warns even without `--size`, with Play Asset Delivery as the fix; N-SYMBOLS when native code ships without debug symbols; I-PROTECT when Automatic protection is on; W-VERIFY takes `console_guide.py mark verification` as evidence; W-TESTING-GATE reads `console.yaml testing_gate_shown` (the Dashboard) ahead of the account date, which the Console does not show. `precheck.json` is written next to PRECHECK.md for the guide.
- Console guide: which developer account (`developer_account_id`, `console_user_index`, `app_id`; links built from them); package name and Check availability on Create app; Automatic protection; the Dashboard testing gate; main store listing (API or the Console editor path); payments profile, in-app products and promo codes for apps that sell digital goods; App access derived from facts (no accounts, a grown-up gate, paid content with promo codes, the partner-testing box); every precheck permission declaration; Families specifics; the upload certificate compared with the AAB's signer; no "accept Play App Signing" step (check "Releases are signed by Google Play"); dragging the AAB in; confirming the draft release and Send for review; the invite step handed wholly to the person with Draft apps and financial permissions; countries are Console-only.
- References: current Console menu names, the order of the paid-content chain (merchant account → products → promo codes → App access → Target audience → Data safety), and the rule that the person presses buttons that grant, accept, attest, save or commit.

## 1.0.1 (2026-10-19)

Fixes from the first real run (Kids Love Science, Gradle, Billing 9.1).

- First run: SKILL.md says how to create the venv. A script started without PyYAML or cryptography prints the install command and exits 1, no traceback.
- `validate_kit.py KIT --init` is the first step of J1 and J2. It adds the kit's lines to the nearest `.gitignore` (outside git it prints them with the path). `gitignore.snippet` and `credentials.example.yaml` moved out of `templates/kit/`; a hand-copied one in the kit root is removed.
- Every script resolves the kit path first. `validate_kit.py play-store-kit` no longer reports an ignored `private/` as not ignored.
- `build.py bundle` picks a JDK 17+: `project.yaml → java_home`, `JAVA_HOME`, `/usr/libexec/java_home -v 17+`, Android Studio's JBR. It reports the one it used.
- An absolute `output_glob` works, and the AAB path comes back normalised.
- When the build succeeds but no AAB is found, a moved build directory (`layout.buildDirectory`, `buildDir`) is named, the AAB is looked up through Gradle, and the fix (`output_glob`) is printed.
- `build.py inspect --kit` and `precheck.py` read `mapping_path` / `native_symbols_path` from project.yaml.
- W-RESTORE no longer fires on `gms/auth/` classes that Play Billing pulls in; it needs a sign-in entry point (`GoogleSignIn`, `GoogleSignInClient`, `gms/auth/api/identity/`, Credential Manager, an auth SDK); `GoogleSignInAccount` alone comes from play-services-base.
- `.living/PROTOCOL.md` points at `rules.py check --online` and Google sources.
- The Billing Library 2027-08-31 row now reads the same in KNOWLEDGE and rules: v8 deadline, v9 or later required (deprecation FAQ table, checked 2026-10-19).
- The status board lists "extension ends" only when policy-extensions.yaml records that extension.

## 1.0.0 (unreleased)

First release: the phone App Bundle release core.

- Connect a service account and record a capability matrix from edit-free reads.
- Bootstrap a new app Console-first, or adopt an existing one with an ownership policy.
- Pre-check the built AAB: target API per form factor, 64-bit, 16 KB ELF alignment, signer, versionCode, compressed size, Billing Library, permissions, Data safety, GenAI.
- Upload as a draft, send for review, wait on `releaseLifecycleState`, staged rollout, halt, resume, promote.
- Approvals by effect with receipts bound to the plan fingerprint; one edit owner with a lock; append-only journal and reconciliation of unknown outcomes.
- Closed-test gate tracked from dated evidence; guided Console checklist for the steps the API cannot do.
- Rules frozen with provenance in `scripts/rules.json`; contract tests against Google's pinned discovery document.
