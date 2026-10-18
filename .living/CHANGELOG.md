# Knowledge changelog

Append-only provenance log for KNOWLEDGE.md changes. Format:

## YYYY-MM-DD
- ADDED/CHANGED/REMOVED/UNCONFIRMED: <what> — <rationale> — <source URL>

A refresh that finds nothing still logs `- No changes found`.

## 2026-10-18
- ADDED: Sidecar attached; knowledge base and scripts/rules.json seeded from the PRIMARY Google pages in sources.md, all read on this date (plan/RESEARCH.md, second pass, and the Codex review).
- ADDED: Target API since 2026-08-31: mobile 36, Wear and Automotive 35, TV and XR 34; extension to 2026-11-01 — https://developer.android.com/google/play/requirements/target-sdk
- ADDED: Billing Library 8 required for new apps and updates from 2026-08-31 (v7 extension to 2026-11-01) — https://developer.android.com/google/play/billing/deprecation-faq
- ADDED: Contract facts pinned from discovery document revision 20261015 (resumable path, commit default, lifecycle enum prefix) — https://raw.githubusercontent.com/googleapis/google-api-python-client/main/googleapiclient/discovery_cache/documents/androidpublisher.v3.json

## 2026-10-19
- CHANGED: Billing Library 2027-08-31 deadline worded as "v8 deadline, v9 or later required" to match the FAQ table (v8 row: 2027-08-31, extension 2027-11-01) and the status board — https://developer.android.com/google/play/billing/deprecation-faq
- CHANGED: PROTOCOL.md refresh step points at `scripts/rules.py check --online` and Google sources (it named an Apple source and a script that does not exist).
- Re-baselined ORIGINAL.sha256 to SKILL.md 1.0.1.
