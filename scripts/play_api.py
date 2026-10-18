#!/usr/bin/env python3
"""A small Google Play Developer API client (androidpublisher v3; facts in references/play-api.md).

Auth      Service-account JSON key only (`type: service_account`). Path from KIT/private/credentials.yaml
          `service_account_json`, else $PLAY_SERVICE_ACCOUNT_JSON. No application-default credentials and no search
          of the disk for keys. An RS256 JWT (iss = client_email, aud = the token URI, at most 1 h) is exchanged for
          an access token, one per scope; the token is reused until 60 s before it expires. Neither the key nor a
          token is ever printed or logged.
Contract  CONTRACT maps every method the kit calls to its HTTP verb and path, taken from Google's discovery
          document (tests/fixtures/discovery, revision 20261015); tests/test_contract.py checks each entry, so a
          method that is not in the document cannot be called.
Client    call(method_id, params, body=None, query=None) applies the idempotency class of the method (PLAN-FINAL
          §5.3):
            read          GET (and :validate): retry 429 (Retry-After) and 5xx with backoff, up to 5 times;
                          one token refresh on 401.
            idempotent    PUT/PATCH: retried once after a transport error or 5xx, and only when the caller passes
                          `confirm`, a callable that re-reads the edit and returns True when it is still valid.
            reconcilable  commit, uploads, tracks.create, insert: never retried blind. A lost response (transport
                          error, timeout, 5xx) raises UnknownOutcome; a 429 is retried once after Retry-After
                          because Google did not process the request.
            destructive   edits.delete: never retried; a lost response raises UnknownOutcome.
          Errors raise PlayError(status, reason, message). The transport is pluggable (tests inject FakePlay).
Upload    upload_resumable() follows Google's resumable protocol: initiate (X-Upload-Content-Type/-Length) →
          Location → PUT chunks of a 256 KiB multiple with Content-Range → 308 + Range → final 200/201. After a lost
          chunk response it queries the session (`Content-Range: bytes */N`) before sending anything else; a 401
          refreshes the token and queries first too. The session URL goes to `on_session` (the journal) and is
          redacted from every log line.

CLI
  python3 scripts/play_api.py connect KIT [--track production] [--package PKG]         # capability matrix
  python3 scripts/play_api.py releases KIT --track TRACK                               # edit-free status read
  python3 scripts/play_api.py whoami KIT                                               # client_email only
"""
from __future__ import annotations

import argparse
import base64
import dataclasses
import datetime as dt
import json
import os
import pathlib
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT_URL = "https://androidpublisher.googleapis.com/"
TOKEN_URI = "https://oauth2.googleapis.com/token"
SCOPE_PUBLISHER = "https://www.googleapis.com/auth/androidpublisher"
SCOPE_REPORTING = "https://www.googleapis.com/auth/playdeveloperreporting"
JWT_LIFETIME = 3000           # Google allows at most 3600 s
REFRESH_MARGIN = 60
CHUNK_UNIT = 256 * 1024       # resumable chunks must be a multiple of 256 KiB
DEFAULT_CHUNK = 32 * CHUNK_UNIT
MAX_READ_RETRIES = 5
REVIEW_BEHAVIOUR = "ERROR_IF_IN_REVIEW"   # Google's default (CANCEL_IN_REVIEW_AND_SUBMIT) is never sent

EXIT_OK, EXIT_CAPABILITY = 0, 8

# method id -> (verb, path below androidpublisher/v3/, class, upload protocols or None)
CONTRACT = {
    "edits.insert": ("POST", "applications/{packageName}/edits", "reconcilable", None),
    "edits.get": ("GET", "applications/{packageName}/edits/{editId}", "read", None),
    "edits.validate": ("POST", "applications/{packageName}/edits/{editId}:validate", "read", None),
    "edits.commit": ("POST", "applications/{packageName}/edits/{editId}:commit", "reconcilable", None),
    "edits.delete": ("DELETE", "applications/{packageName}/edits/{editId}", "destructive", None),
    "edits.tracks.list": ("GET", "applications/{packageName}/edits/{editId}/tracks", "read", None),
    "edits.tracks.get": ("GET", "applications/{packageName}/edits/{editId}/tracks/{track}", "read", None),
    "edits.tracks.update": ("PUT", "applications/{packageName}/edits/{editId}/tracks/{track}", "idempotent", None),
    "edits.tracks.create": ("POST", "applications/{packageName}/edits/{editId}/tracks", "reconcilable", None),
    "edits.bundles.list": ("GET", "applications/{packageName}/edits/{editId}/bundles", "read", None),
    "edits.bundles.upload": ("POST", "applications/{packageName}/edits/{editId}/bundles", "reconcilable",
                             ("simple", "resumable")),
    "edits.deobfuscationfiles.upload": (
        "POST",
        "applications/{packageName}/edits/{editId}/apks/{apkVersionCode}/deobfuscationFiles/{deobfuscationFileType}",
        "reconcilable", ("simple", "resumable")),
    "edits.listings.list": ("GET", "applications/{packageName}/edits/{editId}/listings", "read", None),
    "edits.images.list": ("GET", "applications/{packageName}/edits/{editId}/listings/{language}/{imageType}",
                          "read", None),
    "edits.details.get": ("GET", "applications/{packageName}/edits/{editId}/details", "read", None),
    "edits.testers.get": ("GET", "applications/{packageName}/edits/{editId}/testers/{track}", "read", None),
    "edits.countryavailability.get": ("GET", "applications/{packageName}/edits/{editId}/countryAvailability/{track}",
                                      "read", None),
    "applications.tracks.releases.list": ("GET", "applications/{packageName}/tracks/{track}/releases", "read", None),
}
# query parameters the kit sends, per method (checked against the discovery document)
QUERY = {
    "edits.commit": ("changesInReviewBehavior",),
    "edits.bundles.upload": ("uploadType",),
    "edits.deobfuscationfiles.upload": ("uploadType",),
}


# ------------------------------------------------------------------------------------------------- errors
class PlayError(Exception):
    """A Google API error: HTTP status, the first machine reason (ErrorInfo or errors[].reason) and the message."""

    def __init__(self, status, reason="", message="", method="", path="", body=None):
        self.status, self.reason, self.message, self.method, self.path = status, reason, message, method, path
        self.body = body or {}
        super().__init__(f"HTTP {status} {method} {path}".strip() + (f" {reason}" if reason else "")
                         + (f" - {message}" if message else ""))

    @classmethod
    def from_response(cls, status, data, method="", path=""):
        err = data.get("error", {}) if isinstance(data, dict) else {}
        reasons = [d.get("reason") for d in err.get("details", []) if isinstance(d, dict) and d.get("reason")]
        reasons += [e.get("reason") for e in err.get("errors", []) if isinstance(e, dict) and e.get("reason")]
        if err.get("status"):
            reasons.append(err["status"])
        return cls(status, reasons[0] if reasons else "", err.get("message", ""), method, path, data)

    def has(self, *needles):
        text = json.dumps(self.body) + " " + self.reason + " " + self.message
        return any(n in text for n in needles)

    @property
    def review_conflict(self):
        """ERROR_IF_IN_REVIEW refused the commit: 400 FAILED_PRECONDITION / CHANGES_ALREADY_IN_REVIEW, edit kept."""
        return self.status == 400 and self.has("CHANGES_ALREADY_IN_REVIEW")


class UnknownOutcome(Exception):
    """A non-idempotent request may or may not have landed. Journal it as `unknown` and reconcile."""

    def __init__(self, method_id, cause, session_url=None):
        self.method_id, self.cause, self.session_url = method_id, cause, session_url
        super().__init__(f"{method_id}: outcome unknown ({cause}); run reconcile before anything else")


class UploadSessionGone(Exception):
    pass


class ConfigError(Exception):
    pass


class TransportError(Exception):
    """Raised by a transport when no HTTP response arrived (timeout, reset, DNS)."""


# ------------------------------------------------------------------------------------------------ redaction
_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
_UPLOAD_ID = re.compile(r"(upload_id=)[^&\s\"']+")
_BEARER = re.compile(r"(Bearer\s+)[\w.\-~+/=]+")
_KEY = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S)


def redact(text: str) -> str:
    """Strip key material, bearer tokens, upload session ids and email addresses from a log line."""
    text = _KEY.sub("[private key]", str(text))
    text = _BEARER.sub(r"\1[token]", text)
    text = _UPLOAD_ID.sub(r"\1[session]", text)
    return _EMAIL.sub("[email]", text)


# ------------------------------------------------------------------------------------------------ credentials
@dataclasses.dataclass
class ServiceAccount:
    client_email: str
    private_key: str = dataclasses.field(repr=False)
    private_key_id: str = ""
    token_uri: str = TOKEN_URI
    path: pathlib.Path | None = None

    @property
    def fingerprint(self):
        import hashlib
        return "sha256:" + hashlib.sha256(f"{self.client_email}|{self.private_key_id}".encode()).hexdigest()[:16]


def load_service_account(path) -> ServiceAccount:
    path = pathlib.Path(path).expanduser()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ConfigError(f"cannot read the service-account JSON at {path}: {e.__class__.__name__}") from None
    if data.get("type") != "service_account":
        raise ConfigError(f"{path} is not a service-account key (type is {data.get('type')!r}); "
                          "only service-account JSON keys are accepted")
    for f in ("client_email", "private_key"):
        if not data.get(f):
            raise ConfigError(f"{path} has no {f}")
    token_uri = data.get("token_uri") or TOKEN_URI
    if token_uri != TOKEN_URI:
        raise ConfigError(f"{path} names token_uri {token_uri!r}; only {TOKEN_URI} is used")
    return ServiceAccount(data["client_email"], data["private_key"], data.get("private_key_id", ""), token_uri, path)


def resolve_credentials(kit=None, env=None) -> ServiceAccount:
    """KIT/private/credentials.yaml `service_account_json`, else $PLAY_SERVICE_ACCOUNT_JSON. Nothing else."""
    env = os.environ if env is None else env
    if kit:
        cfg = pathlib.Path(kit) / "private" / "credentials.yaml"
        if cfg.exists():
            import yaml
            doc = yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}
            if doc.get("service_account_json"):
                return load_service_account(doc["service_account_json"])
    if env.get("PLAY_SERVICE_ACCOUNT_JSON"):
        return load_service_account(env["PLAY_SERVICE_ACCOUNT_JSON"])
    raise ConfigError("no service account: set service_account_json in KIT/private/credentials.yaml "
                      "or PLAY_SERVICE_ACCOUNT_JSON (see references/play-api.md)")


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(sa: ServiceAccount, scope: str, now: float, lifetime: int = JWT_LIFETIME) -> str:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    header = {"alg": "RS256", "typ": "JWT"}
    if sa.private_key_id:
        header["kid"] = sa.private_key_id
    claims = {"iss": sa.client_email, "scope": scope, "aud": sa.token_uri, "iat": int(now),
              "exp": int(now) + min(lifetime, 3600)}
    signing_input = _b64url(json.dumps(header, separators=(",", ":")).encode()) + "." + \
        _b64url(json.dumps(claims, separators=(",", ":")).encode())
    key = serialization.load_pem_private_key(sa.private_key.encode(), password=None)
    sig = key.sign(signing_input.encode(), padding.PKCS1v15(), hashes.SHA256())
    return signing_input + "." + _b64url(sig)


class TokenSource:
    """Access tokens per scope from a service account, refreshed REFRESH_MARGIN s before expiry."""

    def __init__(self, sa: ServiceAccount, transport, now=time.time):
        self.sa, self.transport, self.now = sa, transport, now
        self._cache: dict[str, tuple[str, float]] = {}

    def token(self, scope=SCOPE_PUBLISHER, force=False) -> str:
        tok = self._cache.get(scope)
        if tok and not force and tok[1] - REFRESH_MARGIN > self.now():
            return tok[0]
        assertion = sign_jwt(self.sa, scope, self.now())
        body = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                       "assertion": assertion}).encode()
        status, _, raw = self.transport("POST", self.sa.token_uri,
                                        {"Content-Type": "application/x-www-form-urlencoded"}, body)
        data = _json(raw)
        if status != 200 or "access_token" not in data:
            raise PlayError(status, data.get("error", "") if isinstance(data.get("error"), str) else "",
                            data.get("error_description", "token exchange failed"), "POST", "oauth2/token")
        self._cache[scope] = (data["access_token"], self.now() + int(data.get("expires_in", 3600)))
        return data["access_token"]

    def __repr__(self):  # never show a token
        return f"TokenSource({self.sa.client_email!r})"


# ------------------------------------------------------------------------------------------------ transport
def urllib_transport(method, url, headers, body, timeout=120):
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers or {}), e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
        raise TransportError(e.__class__.__name__) from None


def _json(raw):
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except (ValueError, TypeError):
        return {"_raw": raw[:200].decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)[:200]}


def _header(headers, name):
    for k, v in (headers or {}).items():
        if k.lower() == name.lower():
            return v
    return None


def _retry_after(headers, attempt):
    value = _header(headers, "Retry-After")
    if value and str(value).isdigit():
        return min(int(value), 120)
    return min(2 ** attempt, 32) + random.random()


# ------------------------------------------------------------------------------------------------ client
class Client:
    def __init__(self, tokens=None, transport=urllib_transport, sleep=time.sleep, log=None, root_url=ROOT_URL):
        self.tokens, self.transport, self.sleep, self.root_url = tokens, transport, sleep, root_url
        self.log = log or (lambda line: None)

    # -- urls
    def url(self, method_id, params, query=None, protocol=None):
        verb, template, _, protocols = CONTRACT[method_id]
        path = template
        for name in re.findall(r"{(\w+)}", template):
            if name not in params:
                raise KeyError(f"{method_id}: missing path parameter {name}")
            path = path.replace("{" + name + "}", urllib.parse.quote(str(params[name]), safe=""))
        prefix = "androidpublisher/v3/"
        if protocol:
            if not protocols or protocol not in protocols:
                raise KeyError(f"{method_id} has no {protocol} upload")
            prefix = {"simple": "upload/", "resumable": "resumable/upload/"}[protocol] + prefix
        url = self.root_url + prefix + path
        if query:
            for k in query:
                if k not in QUERY.get(method_id, ()):
                    raise KeyError(f"{method_id}: query parameter {k} is not in the contract")
            url += "?" + urllib.parse.urlencode(query)
        return url

    def _auth(self):
        return {"Authorization": "Bearer " + self.tokens.token(SCOPE_PUBLISHER)} if self.tokens else {}

    def _send(self, method, url, headers, body):
        hdrs = {**headers, **self._auth()}
        self.log(redact(f"{method} {url}"))
        return self.transport(method, url, hdrs, body)

    # -- calls
    def call(self, method_id, params, body=None, query=None, confirm=None):
        verb, _, klass, _ = CONTRACT[method_id]
        if method_id == "edits.commit":
            query = {"changesInReviewBehavior": REVIEW_BEHAVIOUR, **(query or {})}
            if query["changesInReviewBehavior"] != REVIEW_BEHAVIOUR:
                raise ValueError("edits.commit is only sent with ERROR_IF_IN_REVIEW")
        url = self.url(method_id, params, query)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Content-Type": "application/json"} if data is not None else {}
        attempt, refreshed, retried = 0, False, False
        while True:
            try:
                status, rh, raw = self._send(verb, url, headers, data)
            except TransportError as e:
                if klass == "read" and attempt < MAX_READ_RETRIES:
                    attempt += 1
                    self.sleep(_retry_after({}, attempt))
                    continue
                if klass == "idempotent" and not retried and confirm and confirm():
                    retried = True
                    continue
                raise UnknownOutcome(method_id, str(e)) from None
            out = _json(raw)
            if 200 <= status < 300:
                return out
            if status == 401 and not refreshed and klass in ("read", "idempotent") and self.tokens:
                refreshed = True
                self.tokens.token(SCOPE_PUBLISHER, force=True)
                continue
            if status == 429:
                # Google did not process a rate-limited request, so a resend is not a blind replay.
                if klass == "read" and attempt < MAX_READ_RETRIES:
                    attempt += 1
                    self.sleep(_retry_after(rh, attempt))
                    continue
                if klass != "read" and not retried:
                    retried = True
                    self.sleep(_retry_after(rh, 1))
                    continue
            if status >= 500:
                if klass == "read" and attempt < MAX_READ_RETRIES:
                    attempt += 1
                    self.sleep(_retry_after(rh, attempt))
                    continue
                if klass == "idempotent" and not retried and confirm and confirm():
                    retried = True
                    continue
                if klass != "read":
                    raise UnknownOutcome(method_id, f"HTTP {status}")
            raise PlayError.from_response(status, out, verb, url.split("?")[0].replace(self.root_url, ""))

    def releases(self, package, track):
        """Edit-free status: applications.tracks.releases.list (at most 20 releases per track)."""
        return self.call("applications.tracks.releases.list", {"packageName": package, "track": track}).get(
            "releases", [])

    # -- uploads
    def upload_resumable(self, method_id, params, file_path, content_type="application/octet-stream",
                         chunk_size=DEFAULT_CHUNK, session_url=None, on_session=None):
        """Upload a file through a resumable session; returns the final JSON (e.g. Bundle{versionCode, sha256}).

        Pass `session_url` to continue a session recorded in the journal; the session is queried first."""
        if chunk_size % CHUNK_UNIT:
            raise ValueError("chunk_size must be a multiple of 256 KiB")
        path = pathlib.Path(file_path)
        total = path.stat().st_size
        if session_url is None:
            url = self.url(method_id, params, {"uploadType": "resumable"}, protocol="resumable")
            try:
                status, rh, raw = self._send("POST", url, {"X-Upload-Content-Type": content_type,
                                                           "X-Upload-Content-Length": str(total),
                                                           "Content-Length": "0"}, b"")
            except TransportError as e:
                # No session URL came back, so nothing can have been uploaded.
                raise PlayError(0, "TRANSPORT", f"could not start the upload session ({e})") from None
            if status == 429:
                self.sleep(_retry_after(rh, 1))
                status, rh, raw = self._send("POST", url, {"X-Upload-Content-Type": content_type,
                                                           "X-Upload-Content-Length": str(total),
                                                           "Content-Length": "0"}, b"")
            if status != 200 or not _header(rh, "Location"):
                raise PlayError.from_response(status, _json(raw), "POST", "resumable " + method_id)
            session_url = _header(rh, "Location")
            if on_session:
                on_session(session_url)
            offset = 0
        else:
            offset = self._query_session(method_id, session_url, total)
            if isinstance(offset, dict):
                return offset
        with path.open("rb") as fh:
            refreshed = False
            while True:
                fh.seek(offset)
                chunk = fh.read(chunk_size)
                end = offset + len(chunk) - 1
                headers = {"Content-Length": str(len(chunk)), "Content-Type": content_type,
                           "Content-Range": f"bytes {offset}-{end}/{total}" if chunk else f"bytes */{total}"}
                try:
                    status, rh, raw = self._send("PUT", session_url, headers, chunk)
                except TransportError as e:
                    result = self._query_after_loss(method_id, session_url, total, str(e))
                    if isinstance(result, dict):
                        return result
                    offset = result
                    continue
                if status in (200, 201):
                    return _json(raw)
                if status == 308:
                    offset = _next_offset(rh)
                    continue
                if status == 401 and not refreshed and self.tokens:
                    refreshed = True
                    self.tokens.token(SCOPE_PUBLISHER, force=True)
                    result = self._query_session(method_id, session_url, total)
                    if isinstance(result, dict):
                        return result
                    offset = result
                    continue
                if status in (404, 410):
                    raise UploadSessionGone(f"{method_id}: upload session expired")
                if status >= 500:
                    result = self._query_after_loss(method_id, session_url, total, f"HTTP {status}")
                    if isinstance(result, dict):
                        return result
                    offset = result
                    continue
                raise PlayError.from_response(status, _json(raw), "PUT", "resumable " + method_id)

    def _query_session(self, method_id, session_url, total):
        """Status query: an int offset to resume from, or the final JSON when the upload already finished."""
        status, rh, raw = self._send("PUT", session_url, {"Content-Length": "0", "Content-Range": f"bytes */{total}"},
                                     b"")
        if status in (200, 201):
            return _json(raw)
        if status == 308:
            return _next_offset(rh)
        if status in (404, 410):
            raise UploadSessionGone(f"{method_id}: upload session expired")
        raise PlayError.from_response(status, _json(raw), "PUT", "resumable status " + method_id)

    def _query_after_loss(self, method_id, session_url, total, cause):
        try:
            return self._query_session(method_id, session_url, total)
        except TransportError:
            raise UnknownOutcome(method_id, cause, session_url) from None


def _next_offset(headers):
    """Next byte to send after a 308. No Range header means Google has nothing yet."""
    rng = _header(headers, "Range")
    if not rng:
        return 0
    m = re.match(r"bytes=0-(\d+)$", rng.strip())
    if not m:
        raise PlayError(308, "BAD_RANGE", f"unexpected Range header {rng!r}")
    return int(m.group(1)) + 1


# ------------------------------------------------------------------------------------------------ connect
def capability_matrix(client: Client, package: str, track: str = "production", now=None) -> dict:
    """Edit-free probe of what the service account can do for one app. Never inserts an edit."""
    now = now or dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    caps = {"checked_at": now, "package": package, "probe": f"applications.tracks.releases.list({track})"}
    try:
        client.releases(package, track)
        caps.update(publisher_api="ok", app_access="ok", app_bootstrapped=True)
    except PlayError as e:
        caps["error"] = {"status": e.status, "reason": e.reason}
        if e.status == 401:
            caps.update(publisher_api="invalid_key")
        elif e.status == 403 and e.has("SERVICE_DISABLED", "accessNotConfigured"):
            caps.update(publisher_api="api_not_enabled")
        elif e.status == 403:
            caps.update(publisher_api="ok", app_access="no_play_grant")
        elif e.status == 404:
            # Google answers 404 both for an app that has no record yet and for one this account cannot see.
            caps.update(publisher_api="ok", app_access="not_found", app_bootstrapped=None,
                        hint="create the app and upload the first AAB in the Console, or grant the service "
                             "account access to this app in Users and permissions")
        else:
            caps.update(publisher_api="error")
    return caps


def _kit_package(kit):
    cfg = pathlib.Path(kit) / "console.yaml"
    if cfg.exists():
        import yaml
        return (yaml.safe_load(cfg.read_text(encoding="utf-8")) or {}).get("package")
    return None


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["connect", "releases", "whoami"])
    p.add_argument("kit_pos", nargs="?", metavar="KIT")
    p.add_argument("--kit", default=None)
    p.add_argument("--package")
    p.add_argument("--track", default="production")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    args.kit = str(pathlib.Path(args.kit or args.kit_pos or "./play-store-kit").resolve())
    package = args.package or _kit_package(args.kit)
    if args.dry_run:
        print(f"dry run: would {args.cmd} {package or '<package>'} on track {args.track}; no network, no auth")
        return EXIT_OK
    try:
        sa = resolve_credentials(args.kit)
    except ConfigError as e:
        print(e, file=sys.stderr)
        return EXIT_CAPABILITY
    if args.cmd == "whoami":
        print(sa.client_email)
        return EXIT_OK
    if not package:
        print("no package: pass --package or set package in KIT/console.yaml", file=sys.stderr)
        return 1
    client = Client(TokenSource(sa, urllib_transport))
    if args.cmd == "releases":
        rel = client.releases(package, args.track)
        print(json.dumps(rel, indent=2) if args.json else
              "\n".join(f"{r.get('releaseName', '?'):<30} {r.get('releaseLifecycleState', '?')}" for r in rel)
              or "no releases")
        return EXIT_OK
    caps = capability_matrix(client, package, args.track)
    caps["principal"] = {"client_email": sa.client_email, "fingerprint": sa.fingerprint}
    live = pathlib.Path(args.kit) / "console-live.json"
    doc = json.loads(live.read_text(encoding="utf-8")) if live.exists() else {}
    doc["capabilities"] = caps
    if live.parent.exists():
        live.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(caps, indent=2))
    return EXIT_OK if caps.get("app_access") == "ok" else EXIT_CAPABILITY


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
