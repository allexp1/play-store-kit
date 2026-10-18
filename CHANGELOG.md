# Changelog

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
