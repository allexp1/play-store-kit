"""play_api: credentials, RS256 tokens, idempotency classes, redaction, capability matrix."""
import base64
import json

import pytest

import play_api
from play_api import PlayError, UnknownOutcome

PKG = {"packageName": "com.example.app"}


def _b64d(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# ---------------------------------------------------------------- credentials
def test_only_service_account_keys(tmp_path):
    p = tmp_path / "user.json"
    p.write_text(json.dumps({"type": "authorized_user", "client_id": "x"}))
    with pytest.raises(play_api.ConfigError, match="not a service-account key"):
        play_api.load_service_account(p)


def test_foreign_token_uri_refused(tmp_path, rsa_pem):
    p = tmp_path / "sa.json"
    p.write_text(json.dumps({"type": "service_account", "client_email": "a@b.iam.gserviceaccount.com",
                             "private_key": rsa_pem[1], "token_uri": "https://evil.example/token"}))
    with pytest.raises(play_api.ConfigError, match="token_uri"):
        play_api.load_service_account(p)


def test_resolve_from_kit_then_env_never_search(tmp_path, sa_file):
    kit = tmp_path / "kit"
    (kit / "private").mkdir(parents=True)
    with pytest.raises(play_api.ConfigError, match="no service account"):
        play_api.resolve_credentials(kit, env={})
    assert play_api.resolve_credentials(kit, env={"PLAY_SERVICE_ACCOUNT_JSON": str(sa_file)}).client_email
    (kit / "private" / "credentials.yaml").write_text(f"service_account_json: {sa_file}\n")
    assert play_api.resolve_credentials(kit, env={}).private_key_id == "kid123"


def test_private_key_not_in_repr(sa_file):
    sa = play_api.load_service_account(sa_file)
    assert "PRIVATE KEY" not in repr(sa)


# ---------------------------------------------------------------- tokens
def test_jwt_is_rs256_and_verifies(sa_file, rsa_pem):
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding

    sa = play_api.load_service_account(sa_file)
    jwt = play_api.sign_jwt(sa, play_api.SCOPE_PUBLISHER, now=1000)
    h, c, s = jwt.split(".")
    header, claims = json.loads(_b64d(h)), json.loads(_b64d(c))
    assert header == {"alg": "RS256", "typ": "JWT", "kid": "kid123"}
    assert claims["iss"] == sa.client_email and claims["aud"] == play_api.TOKEN_URI
    assert claims["scope"] == play_api.SCOPE_PUBLISHER and claims["exp"] - claims["iat"] <= 3600
    rsa_pem[0].public_key().verify(_b64d(s), f"{h}.{c}".encode(), padding.PKCS1v15(), hashes.SHA256())


def test_token_cached_per_scope_and_refreshed(fake, sa_file):
    ts = play_api.TokenSource(play_api.load_service_account(sa_file), fake, now=fake.now)
    a = ts.token()
    assert ts.token() == a and fake.count("token") == 1
    assert ts.token(play_api.SCOPE_REPORTING) != a and fake.count("token") == 2
    fake.advance(3600 - 30)
    assert ts.token() != a


# ---------------------------------------------------------------- idempotency classes
def test_read_retries_5xx_and_429(client, fake):
    fake.fail("applications.tracks.releases.list", 503, times=2)
    fake.fail("applications.tracks.releases.list", 429, headers={"Retry-After": "3"})
    assert client.releases("com.example.app", "production") == []
    assert fake.count("applications.tracks.releases.list") == 4


def test_read_gives_up_after_five(client, fake):
    fake.fail("edits.tracks.list", 500, times=10)
    with pytest.raises(PlayError):
        client.call("edits.tracks.list", {**PKG, "editId": "nope"})
    assert fake.count("edits.tracks.list") == 1 + play_api.MAX_READ_RETRIES


def test_commit_lost_response_is_unknown_never_replayed(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    fake.fail("edits.commit", "lose_after")
    with pytest.raises(UnknownOutcome):
        client.call("edits.commit", {**PKG, "editId": eid})
    assert fake.count("edits.commit") == 1
    assert fake.edits[eid]["state"] == "committed"   # it did land


def test_commit_5xx_is_unknown(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    fake.fail("edits.commit", 502)
    with pytest.raises(UnknownOutcome):
        client.call("edits.commit", {**PKG, "editId": eid})
    assert fake.count("edits.commit") == 1


def test_commit_429_retried_once(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    fake.fail("edits.commit", 429, times=2, headers={"Retry-After": "1"})
    with pytest.raises(PlayError) as e:
        client.call("edits.commit", {**PKG, "editId": eid})
    assert e.value.status == 429 and fake.count("edits.commit") == 2


def test_commit_always_error_if_in_review(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    client.call("edits.commit", {**PKG, "editId": eid})
    assert "changesInReviewBehavior=ERROR_IF_IN_REVIEW" in fake.calls[-1][2]
    with pytest.raises(ValueError):
        client.call("edits.commit", {**PKG, "editId": eid}, query={"changesInReviewBehavior": "CANCEL_IN_REVIEW_AND_SUBMIT"})


def test_idempotent_put_retried_only_with_confirm(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    p = {**PKG, "editId": eid, "track": "qa"}
    fake.fail("edits.tracks.update", 500)
    with pytest.raises(UnknownOutcome):
        client.call("edits.tracks.update", p, body={"track": "qa", "releases": []})
    fake.fail("edits.tracks.update", 500)
    assert client.call("edits.tracks.update", p, body={"track": "qa", "releases": []}, confirm=lambda: True)
    assert fake.count("edits.tracks.update") == 3


def test_destructive_never_retried(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    fake.fail("edits.delete", "lose_before")
    with pytest.raises(UnknownOutcome):
        client.call("edits.delete", {**PKG, "editId": eid})
    assert fake.count("edits.delete") == 1


def test_401_refreshes_once_for_reads(client, fake):
    client.releases("com.example.app", "production")
    fake.revoke_tokens()
    assert client.releases("com.example.app", "production") == []
    assert fake.count("token") == 2


def test_401_not_resent_for_commit(client, fake):
    eid = client.call("edits.insert", PKG, body={})["id"]
    fake.revoke_tokens()
    with pytest.raises(PlayError) as e:
        client.call("edits.commit", {**PKG, "editId": eid})
    assert e.value.status == 401 and fake.count("edits.commit") == 1


def test_review_conflict_detected(client, fake):
    fake.apps["com.example.app"].review_open = True
    eid = client.call("edits.insert", PKG, body={})["id"]
    with pytest.raises(PlayError) as e:
        client.call("edits.commit", {**PKG, "editId": eid})
    assert e.value.review_conflict and e.value.status == 400
    assert fake.edits[eid]["state"] == "open"   # the edit stays valid


def test_unknown_query_parameter_refused(client):
    with pytest.raises(KeyError):
        client.url("edits.commit", {**PKG, "editId": "e"}, {"changesNotSentForReview": "true"})


# ---------------------------------------------------------------- redaction
def test_redact():
    line = ("Authorization: Bearer ya29.abc-DEF_123 https://x/upload?uploadType=resumable&upload_id=AbC123 "
            "tester alice@example.com -----BEGIN PRIVATE KEY-----\nMIIE\n-----END PRIVATE KEY-----")
    out = play_api.redact(line)
    for secret in ("ya29.abc", "AbC123", "alice@example.com", "MIIE"):
        assert secret not in out


def test_logs_are_redacted(fake, sa_file):
    lines = []
    sa = play_api.load_service_account(sa_file)
    c = play_api.Client(play_api.TokenSource(sa, fake, now=fake.now), transport=fake, log=lines.append)
    c.releases("com.example.app", "production")
    assert lines and all("tok-" not in l for l in lines)


# ---------------------------------------------------------------- capability matrix
def test_capabilities_ok_never_inserts(client, fake):
    caps = play_api.capability_matrix(client, "com.example.app")
    assert caps["app_access"] == "ok" and fake.count("edits.insert") == 0


def test_capabilities_not_found(client, fake):
    caps = play_api.capability_matrix(client, "com.example.missing")
    assert caps["app_access"] == "not_found" and "Console" in caps["hint"]


@pytest.mark.parametrize("status, body, field, value", [
    (403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "details": [{"reason": "SERVICE_DISABLED"}]}},
     "publisher_api", "api_not_enabled"),
    (403, {"error": {"code": 403, "status": "PERMISSION_DENIED", "message": "The caller does not have permission"}},
     "app_access", "no_play_grant"),
])
def test_capabilities_403(status, body, field, value):
    def transport(method, url, headers, data):
        return status, {}, json.dumps(body).encode()
    caps = play_api.capability_matrix(play_api.Client(None, transport, sleep=lambda s: None), "com.x")
    assert caps[field] == value
