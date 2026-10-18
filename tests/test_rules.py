"""rules.json is frozen from rules.src.yaml, valid, and never invents unpublished values."""
import json

import pytest

import rules


def test_frozen_file_is_current():
    assert rules.OUT.read_text(encoding="utf-8") == rules.render(rules.build_from_src())


def test_load_and_value():
    r = rules.load()
    assert rules.value(r, "target_sdk.mobile.min") == 36
    assert rules.value(r, "version_code.max") == 2100000000
    assert rules.value(r, "page_size.alignment_bytes") == 16384
    with pytest.raises(KeyError):
        rules.value(r, "no.such.rule")


def test_unverified_rules_have_no_value():
    r = rules.load()
    assert r["edit.expiry_seconds"]["status"] == "unverified"
    assert rules.value(r, "edit.expiry_seconds") is None
    for rule in r.values():
        if rule["status"] == "unverified":
            assert rule["value"] is None


def _one(**over):
    base = {f: None for f in rules.FIELDS}
    base.update(id="x", value=1, unit="u", source_url="https://developer.android.com/x",
                source_section="§1", fetched_at="2026-10-18", reviewed_by="r",
                review_decision="accepted", status="verified")
    base.update(over)
    return base


@pytest.mark.parametrize("over, msg", [
    ({"value": None}, "verified rule without a value"),
    ({"status": "unverified"}, "must have value null"),
    ({"source_url": "https://example.com/blog"}, "not a Google primary source"),
    ({"review_decision": "maybe"}, "review_decision"),
    ({"reviewed_by": ""}, "missing reviewed_by"),
])
def test_validation_rejects(over, msg):
    errors = rules._validate([_one(**over)])
    assert any(msg in e for e in errors), errors


def test_duplicate_ids_rejected():
    assert any("duplicate" in e for e in rules._validate([_one(), _one()]))


def test_freeze_check_detects_stale(tmp_path, monkeypatch):
    stale = tmp_path / "rules.json"
    stale.write_text(json.dumps({"schema": "play-store-kit/rules/1", "rules": []}))
    monkeypatch.setattr(rules, "OUT", stale)
    assert rules.main(["freeze", "--check"]) == 1
    assert rules.main(["freeze"]) == 0
    assert rules.main(["freeze", "--check"]) == 0


def test_check_online_never_rewrites(tmp_path, monkeypatch):
    out = tmp_path / "rules.json"
    out.write_text(rules.OUT.read_text(encoding="utf-8"))
    monkeypatch.setattr(rules, "OUT", out)
    monkeypatch.setattr(rules, "_fetch_sha256", lambda url, timeout=20.0: "0" * 64)
    before = out.read_text()
    assert rules.main(["check", "--online"]) == 0
    assert out.read_text() == before
