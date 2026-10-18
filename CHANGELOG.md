# Changelog

## 1.0.0 (unreleased)

First release: the phone App Bundle release core.

- Connect a service account and record a capability matrix from edit-free reads.
- Bootstrap a new app Console-first, or adopt an existing one with an ownership policy.
- Pre-check the built AAB: target API per form factor, 64-bit, 16 KB ELF alignment, signer, versionCode, compressed size, Billing Library, permissions, Data safety, GenAI.
- Upload as a draft, send for review, wait on `releaseLifecycleState`, staged rollout, halt, resume, promote.
- Approvals by effect with receipts bound to the plan fingerprint; one edit owner with a lock; append-only journal and reconciliation of unknown outcomes.
- Closed-test gate tracked from dated evidence; guided Console checklist for the steps the API cannot do.
- Rules frozen with provenance in `scripts/rules.json`; contract tests against Google's pinned discovery document.
