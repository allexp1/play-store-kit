"""FakePlay: an in-memory Google Play Developer API that models Google's lifecycle, not the kit's assumptions.

Plug it in as a transport: `Client(TokenSource(sa, fake), transport=fake)`. Every request is logged in
`fake.calls` as (method_id, verb, url). Test controls:

  fake.add_app(pkg, tracks=..., bundles=..., publishing="standard"|"managed")
  fake.fail(method_id, mode, times=1)   # method_id "edits.bundles.upload#session" targets the chunk PUTs; mode: "lose_after" (process, then drop the response),
                                        # "lose_before" (drop the request), or an int HTTP status (+ headers)
  fake.finish_review(pkg, approve=True) # IN_REVIEW -> PUBLISHED (standard) / APPROVED_NOT_PUBLISHED (managed)
  fake.console_change(pkg)              # a person saves in the Console: open API edits become invalid
  fake.other_user_commit(pkg)           # another principal commits: open edits become invalid
  fake.advance(seconds)                 # move the clock (edit expiry, token expiry)
  fake.revoke_tokens()                  # every issued token now answers 401

Modelled facts (RESEARCH §3.2–§3.6, REVIEW-codex.md): one open edit per user per app (a new insert supersedes
the old one, which then answers 404); edits carry an absolute expiryTimeSeconds; ERROR_IF_IN_REVIEW refuses a
commit while a review is open with 400 FAILED_PRECONDITION / CHANGES_ALREADY_IN_REVIEW and keeps the edit valid;
the default CANCEL_IN_REVIEW_AND_SUBMIT cancels that review; release status and releaseLifecycleState are
separate; userFraction only with inProgress/halted and strictly between 0 and 1; the first production release
cannot be staged; version codes are never reused; resumable uploads answer 308 with Range.
"""
from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
import urllib.parse

import play_api
from play_api import TransportError

LIFECYCLE = "RELEASE_LIFECYCLE_STATE_"


def _err(status, message, grpc, reason=None):
    err = {"code": status, "message": message, "status": grpc}
    if reason:
        err["details"] = [{"@type": "type.googleapis.com/google.rpc.ErrorInfo", "reason": reason}]
        err["errors"] = [{"reason": reason, "message": message}]
    return status, {}, json.dumps({"error": err}).encode()


def _ok(data, status=200, headers=None):
    return status, headers or {}, json.dumps(data).encode() if data is not None else b""


def _routes():
    table = []
    for mid, (verb, template, _, _) in play_api.CONTRACT.items():
        names = re.findall(r"{(\w+)}", template)
        rx = "^" + re.escape(template)
        for n in names:
            rx = rx.replace(re.escape("{" + n + "}"), r"(?P<%s>[^/:?]+)" % n)
        table.append((verb, re.compile(rx + "$"), mid))
    # longest templates first so ".../tracks/{track}" wins over ".../tracks"
    return sorted(table, key=lambda r: -len(r[1].pattern))


class App:
    def __init__(self, package, tracks, bundles, publishing, draft=False):
        self.package = package
        self.draft = draft              # never published: Play accepts only draft releases
        self.draft_permission = True    # the principal holds "Edit and delete draft apps"
        self.listings: dict[str, dict] = {}
        self.details: dict = {}
        self.images: dict[tuple[str, str], list[str]] = {}
        self.tracks = tracks
        self.bundles = bundles          # [{"versionCode": int, "sha256": str, "sha1": str}]
        self.publishing = publishing
        self.review_open = False
        self.lifecycle: dict[tuple[str, str], str] = {}
        self.cancelled_reviews = 0
        self.production_ever_published = any(
            r.get("status") == "completed" for r in tracks.get("production", {}).get("releases", []))


class FakePlay:
    def __init__(self, start=1_800_000_000.0, edit_lifetime=3600):
        self.clock = start
        self.edit_lifetime = edit_lifetime
        self.apps: dict[str, App] = {}
        self.edits: dict[str, dict] = {}
        self.sessions: dict[str, dict] = {}
        self.calls: list[tuple[str, str, str]] = []
        self.faults: list[dict] = []
        self.tokens: set[str] = set()
        self._ids = itertools.count(1)
        self._routes = _routes()
        self.root = play_api.ROOT_URL

    # ---------------------------------------------------------------- controls
    def now(self):
        return self.clock

    def advance(self, seconds):
        self.clock += seconds

    def add_app(self, package, tracks=None, bundles=None, publishing="standard", draft=False):
        tracks = tracks if tracks is not None else {"production": {"releases": []}, "beta": {"releases": []},
                                                     "qa": {"releases": []}}
        tracks = {name: {"track": name, "releases": copy.deepcopy(t.get("releases", []))}
                  for name, t in tracks.items()}
        self.apps[package] = App(package, tracks, list(bundles or []), publishing, draft)
        app = self.apps[package]
        for name, t in tracks.items():
            for r in t["releases"]:
                app.lifecycle[(name, self._rname(r))] = LIFECYCLE + (
                    "PUBLISHED" if r.get("status") in ("completed", "inProgress", "halted") else "DRAFT")
        return app

    def fail(self, method_id, mode, times=1, headers=None):
        self.faults.append({"method": method_id, "mode": mode, "times": times, "headers": headers or {}})

    def finish_review(self, package, approve=True):
        app = self.apps[package]
        for key, state in app.lifecycle.items():
            if state == LIFECYCLE + "IN_REVIEW":
                if not approve:
                    app.lifecycle[key] = LIFECYCLE + "NOT_APPROVED"
                elif app.publishing == "managed":
                    app.lifecycle[key] = LIFECYCLE + "APPROVED_NOT_PUBLISHED"
                else:
                    app.lifecycle[key] = LIFECYCLE + "PUBLISHED"
        app.review_open = False

    def console_change(self, package):
        for e in self.edits.values():
            if e["package"] == package and e["state"] == "open":
                e["state"] = "invalid"

    other_user_commit = console_change

    def revoke_tokens(self):
        self.tokens.clear()

    def count(self, method_id):
        return sum(1 for c in self.calls if c[0] == method_id)

    def writes(self):
        return [c for c in self.calls if c[1] != "GET" and c[0] not in ("token", "edits.validate")]

    # ---------------------------------------------------------------- transport
    def __call__(self, method, url, headers, body):
        if url == play_api.TOKEN_URI:
            self.calls.append(("token", method, url))
            form = urllib.parse.parse_qs((body or b"").decode())
            if form.get("grant_type") != ["urn:ietf:params:oauth:grant-type:jwt-bearer"] or not form.get("assertion"):
                return _ok({"error": "invalid_grant", "error_description": "bad assertion"}, 400)
            tok = f"tok-{next(self._ids)}"
            self.tokens.add(tok)
            return _ok({"access_token": tok, "expires_in": 3600, "token_type": "Bearer"})
        parsed = urllib.parse.urlsplit(url)
        query = dict(urllib.parse.parse_qsl(parsed.query))
        path = parsed.path.lstrip("/")
        if "upload_id" in query:
            mid = self._session_method(query["upload_id"])
        else:
            mid, params = self._route(method, path)
        self.calls.append((mid, method, url))
        auth = (headers or {}).get("Authorization", "")
        if not auth.startswith("Bearer ") or auth[7:] not in self.tokens:
            return _err(401, "Request had invalid authentication credentials.", "UNAUTHENTICATED")
        fault = self._take_fault(mid)
        if fault and fault["mode"] == "lose_before":
            raise TransportError("timeout")
        if fault and isinstance(fault["mode"], int):
            return fault["mode"], fault["headers"], json.dumps(
                {"error": {"code": fault["mode"], "message": "injected", "status": "INJECTED"}}).encode()
        if "upload_id" in query:
            result = self._session_put(query["upload_id"], headers, body)
        else:
            result = self._dispatch(mid, method, path, params, query, headers, body)
        if fault and fault["mode"] == "lose_after":
            raise TransportError("connection reset after the request was processed")
        return result

    def _take_fault(self, mid):
        for f in self.faults:
            if f["method"] == mid and f["times"] > 0:
                f["times"] -= 1
                return f
        return None

    def _route(self, verb, path):
        for prefix in ("resumable/upload/androidpublisher/v3/", "upload/androidpublisher/v3/", "androidpublisher/v3/"):
            if path.startswith(prefix):
                rest = path[len(prefix):]
                for v, rx, mid in self._routes:
                    m = rx.match(rest)
                    if m and v == verb:
                        return mid, {k: urllib.parse.unquote(x) for k, x in m.groupdict().items()} | {
                            "_protocol": prefix.split("/")[0]}
        raise AssertionError(f"FakePlay: no contract method for {verb} {path}")

    # ---------------------------------------------------------------- helpers
    @staticmethod
    def _rname(r):
        return r.get("name") or ",".join(r.get("versionCodes", []))

    def _edit(self, params):
        e = self.edits.get(params.get("editId"))
        if e is None or e["package"] != params["packageName"]:
            return None, _err(404, "Edit not found.", "NOT_FOUND")
        if e["state"] == "open" and e["expiry"] <= self.clock:
            e["state"] = "expired"
        if e["state"] in ("superseded", "deleted", "committed", "expired"):
            return None, _err(404, "This Edit has been deleted.", "NOT_FOUND")
        if e["state"] == "invalid":
            return None, _err(400, "This edit is no longer valid because changes were made elsewhere.",
                              "INVALID_ARGUMENT", "editInvalidated")
        return e, None

    def _app(self, package):
        app = self.apps.get(package)
        if app is None:
            return None, _err(404, "Package not found: " + package, "NOT_FOUND", "applicationNotFound")
        return app, None

    def _all_version_codes(self, app, edit=None):
        codes = {b["versionCode"] for b in app.bundles}
        if edit:
            codes |= {b["versionCode"] for b in edit["bundles"]}
        return codes

    # ---------------------------------------------------------------- dispatch
    def _dispatch(self, mid, verb, path, params, query, headers, body):
        app, err = self._app(params["packageName"])
        if err:
            return err
        data = json.loads(body) if body else {}
        if mid == "applications.tracks.releases.list":
            track = app.tracks.get(params["track"])
            if track is None:
                return _err(404, "Track not found.", "NOT_FOUND")
            out = []
            for r in track["releases"]:
                out.append({"releaseName": self._rname(r), "track": params["track"],
                            "activeArtifacts": [{"versionCode": int(v)} for v in r.get("versionCodes", [])],
                            "releaseLifecycleState": app.lifecycle.get((params["track"], self._rname(r)),
                                                                       LIFECYCLE + "DRAFT")})
            return _ok({"releases": out[:20]})
        if mid == "edits.insert":
            for e in self.edits.values():
                if e["package"] == app.package and e["state"] == "open":
                    e["state"] = "superseded"
            eid = f"edit-{next(self._ids)}"
            self.edits[eid] = {"id": eid, "package": app.package, "state": "open",
                               "expiry": self.clock + self.edit_lifetime, "tracks": copy.deepcopy(app.tracks),
                               "bundles": [], "deobfuscation": [], "listings": copy.deepcopy(app.listings),
                               "details": copy.deepcopy(app.details), "images": copy.deepcopy(app.images),
                               "store_changed": False}
            return _ok({"id": eid, "expiryTimeSeconds": str(int(self.clock + self.edit_lifetime))})
        if mid == "edits.get":
            # An invalidated edit still exists; only validate and commit fail (RESEARCH §3.2).
            e = self.edits.get(params.get("editId"))
            if e is not None and e["state"] == "invalid" and e["expiry"] > self.clock:
                return _ok({"id": e["id"], "expiryTimeSeconds": str(int(e["expiry"]))})
        edit, err = self._edit(params)
        if err:
            return err
        if mid == "edits.get":
            return _ok({"id": edit["id"], "expiryTimeSeconds": str(int(edit["expiry"]))})
        if mid == "edits.validate":
            if edit["store_changed"] and app.draft and not app.draft_permission:
                return _err(403, "The caller does not have permission", "PERMISSION_DENIED")
            return _ok({"id": edit["id"], "expiryTimeSeconds": str(int(edit["expiry"]))})
        if mid == "edits.delete":
            edit["state"] = "deleted"
            return _ok(None, 204)
        if mid == "edits.tracks.list":
            return _ok({"kind": "androidpublisher#tracksListResponse",
                        "tracks": [copy.deepcopy(t) for t in edit["tracks"].values()]})
        if mid == "edits.tracks.get":
            t = edit["tracks"].get(params["track"])
            return _ok(copy.deepcopy(t)) if t else _err(404, "Track not found.", "NOT_FOUND")
        if mid == "edits.tracks.update":
            return self._update_track(app, edit, params["track"], data)
        if mid == "edits.tracks.create":
            name = data.get("track")
            if data.get("type") != "closedTesting" or not name:
                return _err(400, "Only closedTesting tracks can be created.", "INVALID_ARGUMENT")
            if name in edit["tracks"]:
                return _err(409, "Track already exists.", "ALREADY_EXISTS")
            edit["tracks"][name] = {"track": name, "releases": []}
            return _ok({"track": name, "type": "closedTesting", "formFactor": data.get("formFactor", "DEFAULT")})
        if mid == "edits.bundles.list":
            return _ok({"kind": "androidpublisher#bundlesListResponse",
                        "bundles": [dict(b) for b in app.bundles + edit["bundles"]]})
        if mid == "edits.bundles.upload":
            if params["_protocol"] == "resumable":
                return self._initiate(mid, edit, headers, query)
            return self._finish_bundle(app, edit, body or b"")
        if mid == "edits.deobfuscationfiles.upload":
            if params["_protocol"] == "resumable":
                return self._initiate(mid, edit, headers, query, extra=params)
            edit["deobfuscation"].append(params["apkVersionCode"])
            return _ok({"deobfuscationFile": {"symbolType": params["deobfuscationFileType"]}})
        if mid == "edits.commit":
            return self._commit(app, edit, query)
        if mid == "edits.listings.list":
            return _ok({"listings": [copy.deepcopy(v) for _, v in sorted(edit["listings"].items())]})
        if mid == "edits.listings.update":
            title = data.get("title") or ""
            if len(title) > 30:
                return _err(400, "Title is too long.", "INVALID_ARGUMENT")
            edit["listings"][params["language"]] = {**data, "language": params["language"]}
            edit["store_changed"] = True
            return _ok(edit["listings"][params["language"]])
        if mid == "edits.details.get":
            return _ok(copy.deepcopy(edit["details"]))
        if mid == "edits.details.update":
            edit["details"] = dict(data)
            edit["store_changed"] = True
            return _ok(edit["details"])
        if mid == "edits.images.deleteall":
            gone = edit["images"].pop((params["language"], params["imageType"]), [])
            edit["store_changed"] = True
            return _ok({"deleted": [{"id": i} for i in gone]})
        if mid == "edits.images.upload":
            if params["_protocol"] == "resumable":
                return self._initiate(mid, edit, headers, query, extra=params)
            return self._finish_image(edit, params, body or b"")
        if mid == "edits.testers.update":
            edit.setdefault("testers", {})[params["track"]] = dict(data)
            edit["store_changed"] = True
            return _ok(data)
        if mid in ("edits.testers.get", "edits.countryavailability.get", "edits.images.list"):
            return _ok({})
        raise AssertionError(f"FakePlay: {mid} not modelled")

    def _update_track(self, app, edit, track, data):
        if track not in edit["tracks"]:
            return _err(404, "Track not found.", "NOT_FOUND")
        known = self._all_version_codes(app, edit)
        for r in data.get("releases", []):
            status, fraction = r.get("status"), r.get("userFraction")
            if status not in ("draft", "inProgress", "halted", "completed"):
                return _err(400, f"Invalid release status {status!r}.", "INVALID_ARGUMENT")
            if app.draft and status != "draft":
                return _err(400, "Only releases with status draft may be created on draft app.", "INVALID_ARGUMENT")
            if fraction is not None and (status not in ("inProgress", "halted") or not 0 < fraction < 1):
                return _err(400, "userFraction is only allowed for inProgress or halted releases, between 0 and 1.",
                            "INVALID_ARGUMENT")
            if status in ("inProgress", "halted") and fraction is None:
                return _err(400, "A staged release needs a userFraction.", "INVALID_ARGUMENT")
            if track == "production" and status == "inProgress" and not app.production_ever_published:
                return _err(400, "The first production release cannot be a staged rollout.", "INVALID_ARGUMENT")
            for v in r.get("versionCodes", []):
                if int(v) not in known:
                    return _err(400, f"Version code {v} has not been uploaded.", "INVALID_ARGUMENT")
        edit["tracks"][track] = {"track": track, "releases": copy.deepcopy(data.get("releases", []))}
        return _ok(edit["tracks"][track])

    def _commit(self, app, edit, query):
        behaviour = query.get("changesInReviewBehavior", "CANCEL_IN_REVIEW_AND_SUBMIT")
        if app.review_open:
            if behaviour == "ERROR_IF_IN_REVIEW":
                return _err(400, "Changes are already in review.", "FAILED_PRECONDITION", "CHANGES_ALREADY_IN_REVIEW")
            app.cancelled_reviews += 1
            for key, state in app.lifecycle.items():
                if state == LIFECYCLE + "IN_REVIEW":
                    app.lifecycle[key] = LIFECYCLE + "NOT_SENT_FOR_REVIEW"
        changed = False
        for name, t in edit["tracks"].items():
            before = {self._rname(r): r for r in app.tracks.get(name, {}).get("releases", [])}
            for r in t["releases"]:
                key = (name, self._rname(r))
                if before.get(self._rname(r)) == r and key in app.lifecycle:
                    continue
                changed = True
                app.lifecycle[key] = LIFECYCLE + ("DRAFT" if r.get("status") == "draft" else "IN_REVIEW")
                if name == "production" and r.get("status") == "completed":
                    app.production_ever_published = True
        if edit["store_changed"] and app.draft and not app.draft_permission:
            return _err(403, "The caller does not have permission", "PERMISSION_DENIED")
        app.tracks = copy.deepcopy(edit["tracks"])
        app.bundles += edit["bundles"]
        app.listings, app.details = copy.deepcopy(edit["listings"]), copy.deepcopy(edit["details"])
        app.images = copy.deepcopy(edit["images"])
        edit["state"] = "committed"
        if changed and any(s == LIFECYCLE + "IN_REVIEW" for s in app.lifecycle.values()):
            app.review_open = True
        for other in self.edits.values():   # a commit invalidates other principals' open edits
            if other is not edit and other["package"] == app.package and other["state"] == "open":
                other["state"] = "invalid"
        return _ok({"id": edit["id"], "expiryTimeSeconds": str(int(edit["expiry"]))})

    # ---------------------------------------------------------------- resumable uploads
    def _initiate(self, mid, edit, headers, query, extra=None):
        if query.get("uploadType") != "resumable":
            return _err(400, "uploadType=resumable required.", "INVALID_ARGUMENT")
        total = int(headers.get("X-Upload-Content-Length", "-1"))
        sid = f"S{next(self._ids)}"
        self.sessions[sid] = {"method": mid, "edit": edit["id"], "total": total, "data": b"", "done": None,
                              "extra": extra or {}, "expired": False}
        loc = f"{self.root}resumable/upload/androidpublisher/v3/applications/{edit['package']}/edits/{edit['id']}" \
              f"/bundles?uploadType=resumable&upload_id={sid}"
        return _ok(None, 200, {"Location": loc})

    def _session_method(self, sid):
        s = self.sessions.get(sid)
        return (s["method"] if s else "upload") + "#session"

    def expire_session(self, sid=None):
        for k, s in self.sessions.items():
            if sid in (None, k):
                s["expired"] = True

    def _session_put(self, sid, headers, body):
        s = self.sessions.get(sid)
        if s is None or s["expired"]:
            return _err(404, "Upload session not found.", "NOT_FOUND")
        if s["done"] is not None:
            return s["done"]
        rng = headers.get("Content-Range", "")
        m = re.match(r"bytes (\d+)-(\d+)/(\d+)$", rng)
        if rng.startswith("bytes */"):
            pass  # status query
        elif m:
            start, end = int(m.group(1)), int(m.group(2))
            if start != len(s["data"]):
                return _err(400, "Chunk does not start at the next expected byte.", "INVALID_ARGUMENT")
            s["data"] += body
            assert len(body) == end - start + 1
        else:
            return _err(400, "Bad Content-Range.", "INVALID_ARGUMENT")
        if len(s["data"]) == s["total"]:
            edit = self.edits[s["edit"]]
            if s["method"] == "edits.bundles.upload":
                s["done"] = self._finish_bundle(self.apps[edit["package"]], edit, s["data"])
            elif s["method"] == "edits.images.upload":
                s["done"] = self._finish_image(edit, s["extra"], s["data"])
            else:
                edit["deobfuscation"].append(s["extra"].get("apkVersionCode"))
                s["done"] = _ok({"deobfuscationFile": {"symbolType": s["extra"].get("deobfuscationFileType")}})
            return s["done"]
        hdrs = {"Range": f"bytes=0-{len(s['data']) - 1}"} if s["data"] else {}
        return 308, hdrs, b""

    def _finish_image(self, edit, params, data):
        sha = hashlib.sha256(data).hexdigest()
        edit["images"].setdefault((params["language"], params["imageType"]), []).append(sha)
        edit["store_changed"] = True
        return _ok({"image": {"id": sha[:12], "sha256": sha, "url": "https://play-lh.example/" + sha[:12]}})

    def _finish_bundle(self, app, edit, data):
        m = re.search(rb"versionCode=(\d+)", data)
        code = int(m.group(1)) if m else 1
        if code in self._all_version_codes(app, edit):
            return _err(403, f"APK specifies a version code that has already been used: {code}.",
                        "PERMISSION_DENIED", "apkUpgradeVersionConflict")
        bundle = {"versionCode": code, "sha256": hashlib.sha256(data).hexdigest(), "sha1": hashlib.sha1(data).hexdigest()}
        edit["bundles"].append(bundle)
        return _ok(bundle)
