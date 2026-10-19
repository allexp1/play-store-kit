#!/usr/bin/env python3
"""Store presence through the API: listing texts, app details and graphics, behind G-META-PUB.

  listing.py lint   KIT [--file listings.json] [--images images.json]          # offline: limits from rules.json
  listing.py probe  KIT [--file …] [--details details.json]                    # each kind in its own throwaway edit
  listing.py plan   KIT --pending none|"<item>"… [--file …] [--details …] [--images …]
  listing.py apply  KIT --receipt …-G-META-PUB.json                            # writes into an edit, validates
  listing.py commit KIT --receipt …-G-META-PUB.json                            # the changes go live

listings.json maps a language to an androidpublisher Listing: {"en-US": {"title", "shortDescription",
"fullDescription", "video"}}. details.json is AppDetails ({"contactEmail", "contactWebsite", "contactPhone",
"defaultLanguage"}). images.json maps a language to image types and files:
{"en-US": {"icon": ["graphics/icon-512.png"], "phoneScreenshots": ["shots/1.png", …]}}; paths are relative to KIT.
A language or image type that is not in a file is left alone; an image type that is listed is replaced as a whole
(deleteall, then upload in the listed order).

`probe` writes the planned texts into a fresh edit per kind, validates and deletes the edit: nothing goes live and
no receipt is needed. It tells a missing permission (403 at validate: "Edit and delete draft apps" for a draft app)
from a bad value before a big change is planned. Images are not probed: an uploaded image stays in the Console's
asset library even when its edit is deleted.

`apply` and `commit` follow release.py: the plan is fingerprinted, the receipt is checked before any request,
the remote listings must still equal the plan's baseline, and the commit sends ERROR_IF_IN_REVIEW. A commit also
sends whatever Play Console lists as ready to send, so the plan records `--pending`.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys

import build
import play_api
import play_edit
import release
import rules as rules_mod
from play_api import PlayError, UnknownOutcome
from play_edit import EXIT_APPROVAL, EXIT_FINDINGS, EXIT_GONE, EXIT_OK, EXIT_STALE, KitExit

TEXT_LIMITS = {"title": "listing.title.max_chars", "shortDescription": "listing.short_description.max_chars",
               "fullDescription": "listing.full_description.max_chars"}
IMAGE_TYPES = ("icon", "featureGraphic", "phoneScreenshots", "sevenInchScreenshots", "tenInchScreenshots",
               "tvScreenshots", "wearScreenshots", "tvBanner")
DETAIL_KEYS = ("contactEmail", "contactWebsite", "contactPhone", "defaultLanguage")
MIME = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".webp": "image/webp"}


def _load_json(kit, name, default):
    if not name:
        p = pathlib.Path(kit) / default
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
    p = pathlib.Path(name)
    p = p if p.is_absolute() else pathlib.Path(kit) / p
    return json.loads(p.read_text(encoding="utf-8"))


def lint(kit, listings: dict, images: dict | None = None) -> list[str]:
    """Problems that Play would refuse, offline. Limits come from rules.json."""
    rules = rules_mod.load()
    out = []
    for lang, li in sorted(listings.items()):
        if (li.get("language") or lang) != lang:
            out.append(f"{lang}: the entry says language {li.get('language')!r}")
        for key, rid in TEXT_LIMITS.items():
            text, limit = li.get(key) or "", rules_mod.value(rules, rid)
            if key != "fullDescription" and not text:
                out.append(f"{lang}: {key} is empty")
            if len(text) > limit:
                out.append(f"{lang}: {key} has {len(text)} characters, the limit is {limit}")
        for key in li:
            if key not in (*TEXT_LIMITS, "language", "video"):
                out.append(f"{lang}: {key} is not a Listing field")
    for lang, kinds in sorted((images or {}).items()):
        for kind, files in kinds.items():
            if kind not in IMAGE_TYPES:
                out.append(f"{lang}: image type {kind!r} is not one of {', '.join(IMAGE_TYPES)}")
                continue
            for f in files:
                p = pathlib.Path(kit) / f
                if not p.exists():
                    out.append(f"{lang}: {kind} file {f} does not exist")
                elif p.suffix.lower() not in MIME:
                    out.append(f"{lang}: {kind} file {f} is not PNG, JPEG or WebP")
            if kind in ("icon", "featureGraphic", "tvBanner") and len(files) != 1:
                out.append(f"{lang}: {kind} takes exactly one image")
    return out


def _latest_snapshot(kit) -> dict:
    snaps = sorted((pathlib.Path(kit) / "private" / "snapshots").glob("*.json"))
    if not snaps:
        raise KitExit(EXIT_STALE, "no snapshot: run `release.py import KIT` first")
    return json.loads(snaps[-1].read_text(encoding="utf-8"))


def _canon_listings(items) -> list:
    return sorted(({k: v for k, v in li.items() if k in (*TEXT_LIMITS, "language", "video") and v}
                   for li in items), key=lambda x: x.get("language", ""))


class ListingRunner(release.Runner):
    # ------------------------------------------------------------------------------------------- probe
    def cmd_probe(self, listings, details):
        """Validate each kind of change in its own throwaway edit. Returns {kind: "ok" | reason}."""
        self._need_client()
        pkg = self.kit.package
        kinds = {}
        if listings:
            kinds["listings"] = lambda par: [self.client.call("edits.listings.update", {**par, "language": lang},
                                                              body={**li, "language": lang})
                                             for lang, li in sorted(listings.items())]
        if details:
            kinds["details"] = lambda par: self.client.call("edits.details.update", par, body=details)
        kinds["empty edit"] = lambda par: None          # tells a permission problem from a value problem
        result = {}
        for kind, write in kinds.items():
            plan_like = {"plan_id": f"probe-{kind}-" + play_edit.utcnow(self.now()).replace(":", "-"),
                         "app": {"package": pkg}, "principal": self.principal()}
            try:
                with self.owner().readonly_edit(plan_like) as (edit_id, _):
                    par = {"packageName": pkg, "editId": edit_id}
                    write(par)
                    self.client.call("edits.validate", par)
                result[kind] = "ok"
            except PlayError as e:
                result[kind] = f"refused: HTTP {e.status} {e.message[:160]}" + (
                    f" (likely cause: {e.explain('edits.validate')})" if e.explain("edits.validate") else "")
            except UnknownOutcome as e:
                result[kind] = f"unknown: {e.cause}"
        for kind, r in result.items():
            self.out(f"{kind:<12} {r}")
        return result

    # ------------------------------------------------------------------------------------------- plan
    def cmd_plan(self, listings, details, images, pending, pending_source="attested"):
        problems = lint(self.kit.path, listings, images)
        if problems:
            raise KitExit(EXIT_FINDINGS, "the listing files have problems; fix them first", problems)
        if not (listings or details or images):
            raise KitExit(EXIT_FINDINGS, "nothing to change: give --file, --details or --images")
        mode = self.kit.publishing_mode()
        scope = release.pending_scope(pending, pending_source, self.now())
        snap = _latest_snapshot(self.kit.path)
        remote = {li.get("language"): li for li in snap.get("listings", [])}
        muts, summary = [], []
        for lang, li in sorted(listings.items()):
            body = {**{k: v for k, v in li.items() if k in (*TEXT_LIMITS, "video")}, "language": lang}
            if _canon_listings([body]) == _canon_listings([remote.get(lang, {"language": lang})]):
                continue
            muts.append({"seq": len(muts) + 1, "method": "edits.listings.update", "class": "idempotent",
                         "language": lang, "body": body})
            summary.append(f"{'new' if lang not in remote else 'changed'} listing {lang}")
        if details:
            body = {k: v for k, v in details.items() if k in DETAIL_KEYS}
            if any((snap.get("details") or {}).get(k) != v for k, v in body.items()):
                muts.append({"seq": len(muts) + 1, "method": "edits.details.update", "class": "idempotent",
                             "body": {**(snap.get("details") or {}), **body}})
                summary.append("app details " + ", ".join(sorted(body)))
        for lang, kinds in sorted((images or {}).items()):
            for kind, files in kinds.items():
                muts.append({"seq": len(muts) + 1, "method": "edits.images.deleteall", "class": "idempotent",
                             "language": lang, "imageType": kind})
                for f in files:
                    muts.append({"seq": len(muts) + 1, "method": "edits.images.upload", "class": "reconcilable",
                                 "language": lang, "imageType": kind, "file": f,
                                 "body_sha256": build.sha256_file(pathlib.Path(self.kit.path) / f)})
                summary.append(f"{len(files)} {kind} for {lang} (replaces the current ones)")
        if not muts:
            raise KitExit(EXIT_OK, "the remote listing already matches; nothing to plan")
        plan = self._new_plan("listing", target={"track_id": "store presence", "summary": summary},
                              publishing_mode=mode, pending_console=scope,
                              remote_baseline={"listings": _canon_listings(snap.get("listings", [])),
                                               "snapshot_id": snap.get("snapshot_id")},
                              mutations=muts, effects=["G-META-PUB"])
        self._save_and_ask(plan)
        raise KitExit(EXIT_APPROVAL, "approval needed (plan saved; nothing was sent)")

    # ------------------------------------------------------------------------------------------- apply
    def cmd_apply(self, receipts):
        plan = play_edit.load_plan(self.kit.path)
        if plan.get("command") != "listing":
            raise KitExit(EXIT_STALE, f"the kit's plan is a {plan.get('command')} plan; this apply is for listings")
        if plan["state"] not in ("planned", "applying"):
            raise KitExit(EXIT_STALE, f"plan {plan['plan_id']} is {plan['state']}; plans are single-use")
        self._receipts(plan, ["G-META-PUB"], receipts)          # before any request
        for m in plan["mutations"]:
            if m.get("file") and build.sha256_file(pathlib.Path(self.kit.path) / m["file"]) != m["body_sha256"]:
                raise KitExit(EXIT_STALE, f"{m['file']} changed since the plan was made; make a new plan")
        self._need_client()
        play_edit.save_plan(self.kit.path, plan)
        self._open_or_resume(plan)
        prog, journal = plan["progress"], self._journal(plan)
        if not prog.get("baseline"):
            got = self.client.call("edits.listings.list", self._params(plan)).get("listings", [])
            if _canon_listings(got) != plan["remote_baseline"]["listings"]:
                self.owner().abort(plan, force=True)
                raise KitExit(EXIT_GONE, "the store listing changed since the plan was made (Console or another "
                                         "client); run import and make a new plan")
            prog["baseline"] = play_edit.utcnow(self.now())
            play_edit.save_plan(self.kit.path, plan)
        for m in plan["mutations"]:
            key = str(m["seq"])
            if prog.get(key):
                continue
            self._mutate(plan, journal, m)
            prog[key] = play_edit.utcnow(self.now())
            play_edit.save_plan(self.kit.path, plan)
        self.owner().validate(plan)
        plan["state"] = "applied"
        play_edit.save_plan(self.kit.path, plan)
        self.out(f"applied: {'; '.join(plan['target']['summary'])} in edit {plan['edit']['id']} (not live yet). "
                 "Next: commit with the G-META-PUB receipt.")
        return plan

    def _mutate(self, plan, journal, m):
        mid = m["method"]
        op = f"{mid}:{m['seq']}"
        par = self._params(plan, **{k: m[k] for k in ("language", "imageType") if k in m})

        def confirm():
            try:
                self.client.call("edits.get", self._params(plan))
                return True
            except (PlayError, UnknownOutcome):
                return False
        try:
            if mid == "edits.images.upload":
                path = pathlib.Path(self.kit.path) / m["file"]
                return journal.run(op, mid, lambda: self.client.upload_resumable(
                    mid, par, path, content_type=MIME[path.suffix.lower()],
                    on_session=lambda u: self._session(plan, op, u)), edit_id=plan["edit"]["id"])
            return journal.run(op, mid, lambda: self.client.call(mid, par, body=m.get("body"), confirm=confirm),
                               edit_id=plan["edit"]["id"])
        except (UnknownOutcome, play_api.UploadSessionGone):
            journal.append(op=op, phase="unknown", method=mid, edit_id=plan["edit"]["id"], reason="response lost")
            self._unknown(plan, f"{mid} ({m.get('language', '')} {m.get('imageType', '')}) lost its response")

    # ------------------------------------------------------------------------------------------- commit
    def cmd_commit(self, receipts):
        plan = play_edit.load_plan(self.kit.path)
        if plan.get("command") != "listing":
            raise KitExit(EXIT_STALE, f"the kit's plan is a {plan.get('command')} plan; this commit is for listings")
        return super().cmd_commit(receipts)


def main(argv=None, runner=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=("lint", "probe", "plan", "apply", "commit"))
    ap.add_argument("kit_pos", nargs="?", metavar="KIT")
    ap.add_argument("--kit", default=None)
    ap.add_argument("--file", help="listings.json (default KIT/listings.json)")
    ap.add_argument("--details")
    ap.add_argument("--images")
    ap.add_argument("--pending", nargs="+")
    ap.add_argument("--pending-source", choices=("attested", "console"), default="attested")
    ap.add_argument("--receipt", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    a.kit = str(pathlib.Path(a.kit or a.kit_pos or "./play-store-kit").resolve())
    try:
        listings = _load_json(a.kit, a.file, "listings.json")
        details = _load_json(a.kit, a.details, "details.json") if a.details else {}
        images = _load_json(a.kit, a.images, "images.json") if a.images else {}
        if a.cmd == "lint":
            problems = lint(a.kit, listings, images)
            print(json.dumps(problems, indent=2, ensure_ascii=False) if a.json else
                  "\n".join(problems) or f"ok: {len(listings)} listing(s)")
            return EXIT_FINDINGS if problems else EXIT_OK
        if a.dry_run:
            print(f"dry run: would {a.cmd} {len(listings)} listing(s); no network, no auth, no writes")
            return EXIT_OK
        r = runner or release.build_runner(a.kit, cls=ListingRunner)
        if a.cmd == "probe":
            res = r.cmd_probe(listings, details)
            return EXIT_OK if all(v == "ok" for v in res.values()) else EXIT_FINDINGS
        if a.cmd == "plan":
            r.cmd_plan(listings, details, images, a.pending, a.pending_source)
        elif a.cmd == "apply":
            r.cmd_apply(a.receipt)
        else:
            r.cmd_commit(a.receipt)
        return EXIT_OK
    except KitExit as e:
        print(e.message, file=sys.stderr)
        if e.detail:
            print(json.dumps(e.detail, indent=2, default=str, ensure_ascii=False), file=sys.stderr)
        return e.code
    except play_api.ConfigError as e:
        print(e, file=sys.stderr)
        return play_edit.EXIT_CAPABILITY
    except PlayError as e:
        print(play_api.redact(str(e)), file=sys.stderr)
        if e.explain():
            print("likely cause: " + e.explain(), file=sys.stderr)
        return EXIT_GONE if e.status == 404 else EXIT_FINDINGS


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
