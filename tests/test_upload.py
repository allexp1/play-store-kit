"""Resumable bundle upload against FakePlay: chunks, 308/Range, lost responses, 401, expired sessions."""
import pytest

import play_api
from play_api import UnknownOutcome, UploadSessionGone

PKG = "com.example.app"
CHUNK = play_api.CHUNK_UNIT


@pytest.fixture
def aab(tmp_path):
    p = tmp_path / "app-release.aab"
    p.write_bytes(b"versionCode=42\n" + b"x" * (CHUNK * 2 + 1000))
    return p


@pytest.fixture
def edit(client):
    return client.call("edits.insert", {"packageName": PKG}, body={})["id"]


def up(client, edit, aab, **kw):
    return client.upload_resumable("edits.bundles.upload", {"packageName": PKG, "editId": edit}, aab,
                                   chunk_size=CHUNK, **kw)


def test_upload_in_chunks(client, fake, edit, aab):
    import hashlib
    sessions = []
    b = up(client, edit, aab, on_session=sessions.append)
    assert b["versionCode"] == 42 and b["sha256"] == hashlib.sha256(aab.read_bytes()).hexdigest()
    assert sessions and fake.count("edits.bundles.upload#session") == 3
    init = [c for c in fake.calls if c[0] == "edits.bundles.upload"][0]
    assert "/resumable/upload/androidpublisher/v3/" in init[2] and "uploadType=resumable" in init[2]


def test_lost_chunk_response_queries_before_resending(client, fake, edit, aab):
    fake.fail("edits.bundles.upload#session", "lose_after")
    assert up(client, edit, aab)["versionCode"] == 42
    # chunk 1 (lost), status query, chunk 2, chunk 3
    assert fake.count("edits.bundles.upload#session") == 4


def test_lost_final_response_is_bound_by_status_query(client, fake, edit, aab):
    fake.fail("edits.bundles.upload#session", "lose_after", times=0)
    calls = {"n": 0}
    real = fake.__call__

    def lose_third(method, url, headers, body):
        if "upload_id" in url and headers.get("Content-Range", "").startswith(f"bytes {CHUNK * 2}-"):
            calls["n"] += 1
            if calls["n"] == 1:
                real(method, url, headers, body)
                raise play_api.TransportError("timeout")
        return real(method, url, headers, body)

    client.transport = lose_third
    b = up(client, edit, aab)
    assert b["versionCode"] == 42 and len(fake.edits[edit]["bundles"]) == 1


def test_lost_response_and_status_query_fail_is_unknown(client, fake, edit, aab):
    fake.fail("edits.bundles.upload#session", "lose_after")
    fake.fail("edits.bundles.upload#session", "lose_before")
    with pytest.raises(UnknownOutcome) as e:
        up(client, edit, aab)
    assert e.value.session_url and "upload_id=" in e.value.session_url


def test_resume_recorded_session(client, fake, edit, aab):
    sessions = []
    fake.fail("edits.bundles.upload#session", "lose_after")
    fake.fail("edits.bundles.upload#session", "lose_before")
    with pytest.raises(UnknownOutcome):
        up(client, edit, aab, on_session=sessions.append)
    b = up(client, edit, aab, session_url=sessions[0])
    assert b["versionCode"] == 42 and len(fake.edits[edit]["bundles"]) == 1


def test_401_mid_upload_refreshes_then_queries(client, fake, edit, aab):
    fake.fail("edits.bundles.upload#session", 401)
    assert up(client, edit, aab)["versionCode"] == 42
    assert fake.count("token") == 2


def test_expired_session(client, fake, edit, aab):
    sessions = []
    fake.fail("edits.bundles.upload#session", "lose_after")
    fake.fail("edits.bundles.upload#session", "lose_before")
    with pytest.raises(UnknownOutcome):
        up(client, edit, aab, on_session=sessions.append)
    fake.expire_session()
    with pytest.raises(UploadSessionGone):
        up(client, edit, aab, session_url=sessions[0])


def test_308_without_range_restarts_at_zero():
    assert play_api._next_offset({}) == 0
    assert play_api._next_offset({"range": "bytes=0-262143"}) == CHUNK
    with pytest.raises(play_api.PlayError):
        play_api._next_offset({"Range": "bytes=5-9"})


def test_version_code_reuse_refused(client, fake, edit, aab):
    up(client, edit, aab)
    with pytest.raises(play_api.PlayError) as e:
        up(client, edit, aab)
    assert e.value.status == 403


def test_chunk_size_must_be_256k_multiple(client, edit, aab):
    with pytest.raises(ValueError):
        client.upload_resumable("edits.bundles.upload", {"packageName": PKG, "editId": edit}, aab, chunk_size=1000)
