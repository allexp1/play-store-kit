# Play Store kit

A skill for Claude Code that takes an Android app to Google Play, and keeps it there.

You say one sentence. It works step by step through the official Google Play Developer API, asks for a yes before
each live effect, and stops only where Google needs you.

| You say | What happens |
|---|---|
| "Connect my Play account" | Walks you through a service account with app-scoped permissions, then records what it can actually do |
| "Put my app on Google Play" | Checks the built App Bundle against Play's current requirements, guides the first Console upload, then takes over through the API |
| "Manage my existing Play app" | Imports tracks and releases read-only and marks what the kit may touch |
| "Ship 1.4 to internal testing" | Uploads the signed AAB as a draft, sends it for review after your yes, and waits on Google's lifecycle state |
| "Roll out to 20 %", "halt", "promote" | One approved transition at a time, built from the live track so nothing else changes |
| "Did the commit go through?" | Reconciles a lost response from Google's own state before anything is retried |
| "Get production access" | Tracks the closed-test requirement from dated evidence and tells you the earliest day to apply |
| "Will Google accept my app?" | Target API per form factor, 64-bit, 16 KB alignment of every native library, signer, versionCode, download size, Billing Library, permissions, Data safety, GenAI rules |

## Safety

- **Approvals by effect.** Uploading, distributing to testers, submitting to production and each rollout step are
  separate questions. Your reply is stored as a receipt bound to the exact plan; a changed plan needs a new yes.
- **One edit owner.** A lock, a persisted edit id with its expiry, and no edit is ever opened just to look at status.
- **Commits never cancel a review in progress** (`ERROR_IF_IN_REVIEW`; Google's default would).
- **Nothing is retried blind.** Every write is journaled; an unknown outcome is reconciled first.
- **Your keys stay yours.** Service-account JSON only, read from a path you give; private data in a gitignored
  `private/` folder.
- The kit never signs in, never accepts terms, never presses Publish in the Console.

## Install

```
npx play-store-kit
```

or from a clone: `./install.sh`. Both install into `~/.claude/skills/play-store-kit/` (`--dest DIR` for another place,
`--dry-run` to see the plan). Needs Python 3.12+ with the requirements in a venv (`python3 -m venv .venv &&
.venv/bin/pip install -r requirements.txt` in the skill folder), a JDK 17+ and `bundletool` for
artifact checks.

## Develop

```
bash test.sh                       # offline tests + installer dry runs
python3 scripts/rules.py show      # the frozen Play rules and their sources
python3 scripts/rules.py check --online   # advisory: did any source page change?
```

Rules are edited in `scripts/rules.src.yaml` and frozen with `python3 scripts/rules.py freeze`. API facts are tested
against Google's discovery document pinned in `tests/fixtures/discovery/`.

## Licence

MIT
