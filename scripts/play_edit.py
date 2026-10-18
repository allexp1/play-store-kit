#!/usr/bin/env python3
"""The edit owner: the only code that inserts, validates, commits or deletes a Play edit (PLAN-FINAL §5).

Lock      KIT/private/locks/<principal>.<package>.lock (pid, host, started, plan id, edit id, expiry), created with
          O_EXCL before edits.insert and removed on commit, abort or expiry. A second process gets exit 6 and the
          lock contents.
Plan      KIT/play-plan.json. The edit id and its absolute expiryTimeSeconds are written the moment edits.insert
          returns, before any other write (write temp + fsync + atomic rename). The plan knows exactly one edit.
Journal   KIT/private/journal/<plan>.jsonl, append-only, fsync per line: {seq, ts, phase, method, path, status,
          response_summary, edit_id}. A `started` line with no outcome means the outcome is unknown.
Commit    always ERROR_IF_IN_REVIEW. A review conflict keeps the edit (exit 4); a lost response is `unknown`
          (exit 5) and must be reconciled before abort or a new plan; a vanished edit is `invalidated` (exit 7).
Status    never inserts an edit; reads that need one reuse the persisted edit (readonly_edit).

CLI
  python3 scripts/play_edit.py open     --kit KIT --plan ID
  python3 scripts/play_edit.py resume   --kit KIT
  python3 scripts/play_edit.py validate --kit KIT
  python3 scripts/play_edit.py abort    --kit KIT [--force]
  python3 scripts/play_edit.py show     --kit KIT                # no network
"""
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import pathlib
import socket
import sys
import time

import play_api
from play_api import PlayError, UnknownOutcome

EXIT_OK, EXIT_FINDINGS, EXIT_APPROVAL, EXIT_STALE, EXIT_REVIEW, EXIT_UNKNOWN, EXIT_LOCK, EXIT_GONE, \
    EXIT_CAPABILITY, EXIT_SCOPE = range(10)

PLAN_FILE = "play-plan.json"
STATES = ("planned", "approved", "applying", "applied", "committing", "committed", "unknown", "invalidated",
          "aborted", "consumed")


class KitExit(Exception):
    def __init__(self, code, message, detail=None):
        self.code, self.message, self.detail = code, message, detail
        super().__init__(message)


def utcnow(now=None):
    return dt.datetime.fromtimestamp(now if now is not None else time.time(), dt.timezone.utc) \
        .strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------------------------------------------ files
def write_atomic(path: pathlib.Path, text: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + f".tmp{os.getpid()}")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def load_plan(kit) -> dict:
    path = pathlib.Path(kit) / PLAN_FILE
    if not path.exists():
        raise KitExit(EXIT_STALE, f"no plan at {path}; make one first")
    return json.loads(path.read_text(encoding="utf-8"))


def save_plan(kit, plan: dict):
    if plan.get("state") not in STATES:
        raise ValueError(f"unknown plan state {plan.get('state')!r}")
    write_atomic(pathlib.Path(kit) / PLAN_FILE, json.dumps(plan, indent=2, ensure_ascii=False) + "\n")


class Journal:
    """Append-only JSONL journal for one plan."""

    def __init__(self, path, now=time.time):
        self.path, self.now = pathlib.Path(path), now
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def entries(self) -> list[dict]:
        if not self.path.exists():
            return []
        return [json.loads(line) for line in self.path.read_text(encoding="utf-8").splitlines() if line.strip()]

    def append(self, **entry) -> dict:
        entry = {"seq": len(self.entries()) + 1, "ts": utcnow(self.now()), **entry}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(play_api.redact(line) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def unresolved(self) -> list[dict]:
        """Operations whose last line is `started` or `unknown` (never followed by an outcome or reconciled)."""
        last: dict[str, dict] = {}
        for e in self.entries():
            last[e["op"]] = e
        return [e for e in last.values() if e["phase"] in ("started", "unknown")]

    def run(self, op, method, fn, edit_id=None, summarize=lambda r: None):
        """Journal `started`, run fn(), then `succeeded`, `failed` or `unknown`. Re-raises."""
        self.append(op=op, phase="started", method=method, edit_id=edit_id)
        try:
            result = fn()
        except UnknownOutcome as e:
            self.append(op=op, phase="unknown", method=method, edit_id=edit_id, reason=str(e.cause),
                        session=bool(e.session_url))
            raise
        except PlayError as e:
            self.append(op=op, phase="failed", method=method, edit_id=edit_id, status=e.status, reason=e.reason)
            raise
        self.append(op=op, phase="succeeded", method=method, edit_id=edit_id, response_summary=summarize(result))
        return result


# ------------------------------------------------------------------------------------------------ lock
class Lock:
    def __init__(self, kit, principal_fp, package, now=time.time):
        safe = principal_fp.replace(":", "-")
        self.path = pathlib.Path(kit) / "private" / "locks" / f"{safe}.{package}.lock"
        self.now = now

    def read(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def expired(self, info) -> bool:
        exp = (info or {}).get("expiry")
        return exp is not None and float(exp) <= self.now()

    def acquire(self, plan_id):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        info = self.read()
        if info is not None:
            if info.get("plan_id") == plan_id:
                return info
            if not self.expired(info):
                raise KitExit(EXIT_LOCK, f"another plan holds the edit lock ({self.path.name})", info)
            self.path.unlink(missing_ok=True)
        info = {"pid": os.getpid(), "host": socket.gethostname(), "started": utcnow(self.now()),
                "plan_id": plan_id, "edit_id": None, "expiry": None}
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise KitExit(EXIT_LOCK, "another process took the edit lock first", self.read()) from None
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
            fh.flush()
            os.fsync(fh.fileno())
        return info

    def update(self, **fields):
        info = {**(self.read() or {}), **fields}
        write_atomic(self.path, json.dumps(info))

    def release(self, plan_id=None):
        info = self.read()
        if info and plan_id and info.get("plan_id") != plan_id:
            return
        self.path.unlink(missing_ok=True)


# ------------------------------------------------------------------------------------------------ owner
class EditOwner:
    def __init__(self, client: play_api.Client, kit, now=time.time):
        self.client, self.kit, self.now = client, pathlib.Path(kit), now

    # -- helpers
    def _ctx(self, plan):
        package = plan["app"]["package"]
        lock = Lock(self.kit, plan["principal"]["fingerprint"], package, self.now)
        journal = Journal(self.kit / plan["journal"], self.now)
        return package, lock, journal

    def _params(self, plan):
        return {"packageName": plan["app"]["package"], "editId": plan["edit"]["id"]}

    def _expired(self, plan):
        exp = plan.get("edit", {}).get("expiry_time_seconds")
        return exp is not None and int(exp) <= self.now()

    def _set_state(self, plan, state):
        plan["state"] = state
        save_plan(self.kit, plan)

    # -- lifecycle
    def open(self, plan) -> dict:
        package, lock, journal = self._ctx(plan)
        if plan.get("edit", {}).get("id") and not self._expired(plan):
            raise KitExit(EXIT_STALE, "this plan already has an edit; use resume")
        if journal.unresolved():
            raise KitExit(EXIT_UNKNOWN, "the journal has unknown outcomes; run reconcile first")
        lock.acquire(plan["plan_id"])
        try:
            edit = journal.run("edits.insert", "edits.insert",
                               lambda: self.client.call("edits.insert", {"packageName": package}, body={}),
                               summarize=lambda r: {"id": r.get("id"), "expiryTimeSeconds": r.get("expiryTimeSeconds")})
        except UnknownOutcome:
            # A lost insert leaves at most an orphan edit, which the next insert by this principal invalidates.
            journal.append(op="edits.insert", phase="reconciled", method="edits.insert",
                           outcome="orphan edit, superseded by the next insert")
            lock.release(plan["plan_id"])
            raise KitExit(EXIT_UNKNOWN, "edits.insert lost its response; nothing else was written, open again")
        except PlayError:
            lock.release(plan["plan_id"])
            raise
        plan["edit"] = {"id": edit["id"], "expiry_time_seconds": int(edit["expiryTimeSeconds"])}
        save_plan(self.kit, plan)  # persisted before any other write
        lock.update(edit_id=edit["id"], expiry=int(edit["expiryTimeSeconds"]))
        return plan["edit"]

    def resume(self, plan) -> dict:
        package, lock, journal = self._ctx(plan)
        if not plan.get("edit", {}).get("id"):
            raise KitExit(EXIT_GONE, "this plan has no edit; open one")
        info = lock.read()
        if not info or info.get("plan_id") != plan["plan_id"]:
            raise KitExit(EXIT_LOCK, "the edit lock is not held by this plan", info)
        if self._expired(plan):
            self._invalidate(plan, lock, journal, "the edit expired")
        try:
            self.client.call("edits.get", self._params(plan))
        except PlayError as e:
            if e.status == 404:
                self._invalidate(plan, lock, journal, "the edit is gone (expired, or replaced by a newer edit)")
            if e.status in (400, 409):
                self._invalidate(plan, lock, journal, f"the edit is no longer valid ({e.reason or e.status})")
            raise
        return plan["edit"]

    def _invalidate(self, plan, lock, journal, why):
        journal.append(op="edit", phase="failed", method="edits.get", edit_id=plan["edit"]["id"], reason=why)
        plan["state"] = "invalidated"
        save_plan(self.kit, plan)
        lock.release(plan["plan_id"])
        raise KitExit(EXIT_GONE, f"{why}; make a new plan")

    def validate(self, plan) -> dict:
        package, lock, journal = self._ctx(plan)
        self.resume(plan)
        try:
            return journal.run("edits.validate", "edits.validate",
                               lambda: self.client.call("edits.validate", self._params(plan)),
                               edit_id=plan["edit"]["id"])
        except PlayError as e:
            if e.status in (400, 404, 409):
                self._invalidate(plan, lock, journal, f"the edit no longer validates ({e.reason or e.status}); "
                                                      "it was probably changed in the Console or by another client")
            raise

    def commit(self, plan) -> dict:
        package, lock, journal = self._ctx(plan)
        if plan.get("state") not in ("applied",):
            raise KitExit(EXIT_STALE, f"plan state is {plan.get('state')!r}; only an applied plan can be committed")
        self.resume(plan)
        edit_id = plan["edit"]["id"]
        self._set_state(plan, "committing")
        try:
            result = journal.run("edits.commit", "edits.commit",
                                 lambda: self.client.call("edits.commit", self._params(plan)), edit_id=edit_id,
                                 summarize=lambda r: {"id": r.get("id")})
        except UnknownOutcome:
            self._set_state(plan, "unknown")
            raise KitExit(EXIT_UNKNOWN, "the commit's response was lost; run reconcile before anything else")
        except PlayError as e:
            if e.review_conflict:
                self._set_state(plan, "applied")  # the edit stays valid
                raise KitExit(EXIT_REVIEW, "changes are already in review; the edit is kept. Wait, then commit "
                                           "again", {"reason": e.reason})
            if e.status in (400, 404, 409):
                # Externally invalidated (Console change, another client's commit) or no longer valid: re-plan.
                self._invalidate(plan, lock, journal, f"Google refused the commit ({e.status} {e.reason}: "
                                                      f"{e.message[:200]})")
            self._set_state(plan, "applied")
            raise
        plan["state"] = "consumed"
        plan["committed_at"] = utcnow(self.now())
        save_plan(self.kit, plan)
        lock.release(plan["plan_id"])
        return result

    def reconcile_commit(self, plan, landed) -> str:
        """Settle an unknown commit. `landed()` reads releases.list and says whether the release is now there.

        Returns 'succeeded', 'applied' (the commit did not land; the edit is still open) or 'unknown'."""
        package, lock, journal = self._ctx(plan)
        try:
            self.client.call("edits.get", self._params(plan))
            outcome = "applied"
        except PlayError as e:
            outcome = "succeeded" if e.status == 404 and landed() else "unknown"
        except UnknownOutcome:
            outcome = "unknown"
        if outcome == "unknown":
            return outcome
        journal.append(op="edits.commit", phase="reconciled", method="edits.commit",
                       edit_id=plan["edit"]["id"], outcome=outcome)
        if outcome == "succeeded":
            plan["state"] = "consumed"
            save_plan(self.kit, plan)
            lock.release(plan["plan_id"])
        else:
            self._set_state(plan, "applied")
        return outcome

    def abort(self, plan, force=False) -> None:
        package, lock, journal = self._ctx(plan)
        if journal.unresolved():
            raise KitExit(EXIT_UNKNOWN, "the journal has unknown outcomes; run reconcile before abort",
                          journal.unresolved())
        edit_id = plan.get("edit", {}).get("id")
        if edit_id:
            try:
                journal.run("edits.delete", "edits.delete",
                            lambda: self.client.call("edits.delete", self._params(plan)), edit_id=edit_id)
            except PlayError as e:
                if e.status != 404 and not force:
                    raise
            except UnknownOutcome:
                if not force:
                    raise KitExit(EXIT_UNKNOWN, "edits.delete lost its response; the edit expires on its own")
        plan["state"] = "aborted"
        save_plan(self.kit, plan)
        lock.release(plan["plan_id"])

    @contextlib.contextmanager
    def readonly_edit(self, plan_like):
        """An edit to read inside. Reuses the persisted, unexpired edit (its view includes uncommitted changes);
        otherwise opens one under the lock and deletes it afterwards. Never used by status."""
        persisted = plan_like.get("edit", {})
        if persisted.get("id") and not self._expired(plan_like):
            yield persisted["id"], True
            return
        package = plan_like["app"]["package"]
        lock = Lock(self.kit, plan_like["principal"]["fingerprint"], package, self.now)
        lock.acquire(plan_like["plan_id"])
        try:
            edit = self.client.call("edits.insert", {"packageName": package}, body={})
            lock.update(edit_id=edit["id"], expiry=int(edit["expiryTimeSeconds"]))
            try:
                yield edit["id"], False
            finally:
                try:
                    self.client.call("edits.delete", {"packageName": package, "editId": edit["id"]})
                except (PlayError, UnknownOutcome):
                    pass  # a read-only edit expires on its own
        finally:
            lock.release(plan_like["plan_id"])


# ------------------------------------------------------------------------------------------------ CLI
def show(kit) -> dict:
    plan = load_plan(kit)
    _, lock, journal = EditOwner(None, kit)._ctx(plan)
    return {"plan_id": plan["plan_id"], "state": plan.get("state"), "edit": plan.get("edit"),
            "lock": lock.read(), "unresolved": journal.unresolved(), "journal_tail": journal.entries()[-5:]}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["open", "resume", "validate", "abort", "show"])
    p.add_argument("kit_pos", nargs="?", metavar="KIT")
    p.add_argument("--kit", default=None)
    p.add_argument("--plan")
    p.add_argument("--force", action="store_true")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    args.kit = str(pathlib.Path(args.kit or args.kit_pos or "./play-store-kit").resolve())
    try:
        if args.cmd == "show":
            print(json.dumps(show(args.kit), indent=2))
            return EXIT_OK
        plan = load_plan(args.kit)
        if args.plan and args.plan != plan["plan_id"]:
            raise KitExit(EXIT_STALE, f"the kit's plan is {plan['plan_id']}, not {args.plan}")
        if args.dry_run:
            print(f"dry run: would {args.cmd} the edit of plan {plan['plan_id']}; no network, no writes")
            return EXIT_OK
        client = play_api.Client(play_api.TokenSource(play_api.resolve_credentials(args.kit),
                                                      play_api.urllib_transport))
        owner = EditOwner(client, args.kit)
        result = {"open": owner.open, "resume": owner.resume, "validate": owner.validate}.get(args.cmd)
        if result:
            print(json.dumps(result(plan), indent=2))
        else:
            owner.abort(plan, force=args.force)
            print("aborted")
        return EXIT_OK
    except KitExit as e:
        print(e.message, file=sys.stderr)
        if e.detail:
            print(json.dumps(e.detail, indent=2), file=sys.stderr)
        return e.code
    except play_api.ConfigError as e:
        print(e, file=sys.stderr)
        return EXIT_CAPABILITY
    except PlayError as e:
        print(e, file=sys.stderr)
        return EXIT_FINDINGS


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
