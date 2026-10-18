"""precheck.py: the §10 catalogue on synthetic bundles, boundaries (§15.7) and waivers."""
import datetime as dt

import pytest
import yaml

import build
import precheck
from artifacts import StubBundletool, make_aab, manifest

TODAY = dt.date(2026, 10, 18)
GOOD_FACTS = {"package": "com.example.app", "privacy_policy_url": "https://example.com/privacy",
              "account": {"type": "organization"},
              "declarations": {"digital_goods": False, "genai": False, "financial": False, "health": False,
                               "government": False, "ugc": False, "children_audience": False}}


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "play-store-kit"
    (k / "private").mkdir(parents=True)
    (k / "facts.yaml").write_text(yaml.safe_dump(GOOD_FACTS))
    return k


def write(kit, name, data):
    p = kit / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(data) if not isinstance(data, str) else data)


def rec_for(tmp_path, key, libs=None, dex=b"dex\n035\0", subject="CN=Upload Key,O=Example", signed=True, **m):
    aab = tmp_path / "app.aab"
    sha = make_aab(aab, key, libs=libs, dex=dex, signer_subject=subject, signed=signed)
    r = build.inspect(aab, StubBundletool(manifest(**m)), size=False)
    return r, sha


def ids(findings, severity=None):
    return {f.id for f in findings if not f.waived and (severity is None or f.severity == severity)}


def bound_kit(kit, sha, version_codes=(41,)):
    write(kit, "console.yaml", {"package": "com.example.app", "upload_certificate_sha256": sha})
    import json
    (kit / "console-live.json").write_text(json.dumps({
        "captured_at": "2026-10-18T10:00:00Z", "developer_verification": {"observed_at": "2026-10-18"},
        "tracks": {"production": {"releases": [{"versionCodes": [str(v) for v in version_codes]}]}}}))


def test_clean_bundle_passes(tmp_path, kit, rsa_pem):
    rec, sha = rec_for(tmp_path, rsa_pem[0], libs={"arm64-v8a/liba.so": [16384]})
    bound_kit(kit, sha)
    rec.compressed_download_bytes = {"min": 10_000_000, "max": 20_000_000}
    f = precheck.run(kit, rec, TODAY)
    assert not ids(f, "BLOCKER") and not any(x.counts() for x in f), [(x.id, x.message) for x in f]


@pytest.mark.parametrize("target, ff, sev", [(35, None, "BLOCKER"), (36, None, None), (34, ["tv"], None),
                                             (33, ["tv"], "BLOCKER"), (34, ["wear"], "BLOCKER"),
                                             (35, ["automotive"], None), (34, ["xr"], None)])
def test_target_api_per_form_factor(tmp_path, kit, rsa_pem, target, ff, sev):
    if ff:
        write(kit, "facts.yaml", {**GOOD_FACTS, "target_devices": ff})
    rec, sha = rec_for(tmp_path, rsa_pem[0], target_sdk=target)
    got = {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-TARGET"}
    assert got == ({sev} if sev else set())


def test_target_extension_needs_console_evidence(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], target_sdk=35)
    write(kit, "policy-extensions.yaml", {"extensions": [{"kind": "target_sdk", "until": "2026-11-01", "source": "attested"}]})
    assert "BLOCKER" in {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-TARGET"}
    write(kit, "policy-extensions.yaml", {"extensions": [{"kind": "target_sdk", "until": "2026-11-01", "source": "console"}]})
    assert {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-TARGET"} == {"WARNING"}
    assert "BLOCKER" in {f.severity for f in precheck.run(kit, rec, dt.date(2026, 11, 2)) if f.id == "B-TARGET"}


def test_16k_and_64bit(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], libs={"arm64-v8a/libbad.so": [4096], "armeabi-v7a/libv7.so": [4096]})
    f = precheck.run(kit, rec, TODAY)
    assert "B-16K" in ids(f, "BLOCKER") and "B-64BIT" not in ids(f)
    rec2, _ = rec_for(tmp_path, rsa_pem[0], libs={"armeabi-v7a/libv7.so": [4096]})
    assert "B-64BIT" in ids(precheck.run(kit, rec2, TODAY), "BLOCKER")
    rec3, _ = rec_for(tmp_path, rsa_pem[0], libs={"x86/libx.so": ([4096], False), "arm64-v8a/a.so": [16384]})
    assert "B-64BIT" in ids(precheck.run(kit, rec3, TODAY), "BLOCKER")


def test_signer_rules(tmp_path, kit, rsa_pem):
    rec, sha = rec_for(tmp_path, rsa_pem[0], subject="CN=Android Debug,O=Android,C=US")
    bound_kit(kit, sha)
    assert "B-SIGN" in ids(precheck.run(kit, rec, TODAY), "BLOCKER")
    rec2, _ = rec_for(tmp_path, rsa_pem[0])
    bound_kit(kit, "ab" * 32)
    assert any("not the Console upload certificate" in f.message for f in precheck.run(kit, rec2, TODAY))
    rec3, _ = rec_for(tmp_path, None, signed=False)
    assert any("not signed" in f.message for f in precheck.run(kit, rec3, TODAY))


@pytest.mark.parametrize("code, blocked", [(2_100_000_000, False), (2_100_000_001, True)])
def test_version_code_maximum_inclusive(tmp_path, kit, rsa_pem, code, blocked):
    rec, sha = rec_for(tmp_path, rsa_pem[0], version_code=code)
    bound_kit(kit, sha)
    assert ("B-VERSION" in ids(precheck.run(kit, rec, TODAY), "BLOCKER")) == blocked


def test_version_code_must_exceed_remote(tmp_path, kit, rsa_pem):
    rec, sha = rec_for(tmp_path, rsa_pem[0], version_code=42)
    bound_kit(kit, sha, version_codes=(42,))
    assert "B-VERSION" in ids(precheck.run(kit, rec, TODAY), "BLOCKER")
    (kit / "console-live.json").unlink()
    assert {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-VERSION"} == {"WARNING"}


def test_size_limits(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0])
    rec.compressed_download_bytes = {"min": 1, "max": 501 * 1024 * 1024}
    assert "B-SIZE" in ids(precheck.run(kit, rec, TODAY), "BLOCKER")
    rec.compressed_download_bytes = {"min": 1, "max": 201 * 1024 * 1024}
    assert "B-SIZE" in ids(precheck.run(kit, rec, TODAY), "WARNING")


@pytest.mark.parametrize("version, sev", [("7.1.1", "BLOCKER"), ("8.0.0", "WARNING"), ("9.0.0", None)])
def test_billing_library(tmp_path, kit, rsa_pem, version, sev):
    rec, _ = rec_for(tmp_path, rsa_pem[0], meta={"com.google.android.play.billingclient.version": version})
    got = {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-BILLING-LIB"}
    assert got == ({sev} if sev else set())


def test_billing_sdk_without_version_is_question(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/android/billingclient/api/BillingClient;")
    assert {f.severity for f in precheck.run(kit, rec, TODAY) if f.id == "B-BILLING-LIB"} == {"QUESTION"}


def test_payment_sdk(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/stripe/android/Stripe;")
    write(kit, "facts.yaml", {**GOOD_FACTS, "declarations": {**GOOD_FACTS["declarations"], "digital_goods": True}})
    assert "R-PAY" in ids(precheck.run(kit, rec, TODAY), "LIKELY")
    d = dict(GOOD_FACTS["declarations"])
    d.pop("digital_goods")
    write(kit, "facts.yaml", {**GOOD_FACTS, "declarations": d})
    assert "R-PAY" in ids(precheck.run(kit, rec, TODAY), "QUESTION")


def test_permissions_and_adid(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/google/android/gms/ads/AdView;",
                     permissions=["android.permission.READ_SMS", "android.permission.FOREGROUND_SERVICE_LOCATION",
                                  "android.permission.CAMERA"])
    f = precheck.run(kit, rec, TODAY)
    msgs = " ".join(x.message for x in f if x.id == "R-PERM")
    assert "SMS and Call Log" in msgs and "Foreground service" in msgs and "CAMERA" not in msgs
    assert "R-ADID" in ids(f, "QUESTION")
    rec2, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/google/android/gms/ads/AdView;",
                      permissions=["com.google.android.gms.permission.AD_ID"])
    assert "R-ADID" not in ids(precheck.run(kit, rec2, TODAY))


def test_privacy_questions_block(tmp_path, kit, rsa_pem):
    facts = dict(GOOD_FACTS)
    facts.pop("privacy_policy_url")
    write(kit, "facts.yaml", facts)
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/google/firebase/auth/FirebaseAuth;")
    f = precheck.run(kit, rec, TODAY)
    priv = [x for x in f if x.id == "R-PRIVACY"]
    assert len(priv) == 2 and all(x.blocking for x in priv) and any(x.counts() for x in priv)


def test_genai_checks(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"Lcom/google/firebase/ai/GenerativeModel;")
    d = dict(GOOD_FACTS["declarations"])
    d.pop("genai")
    write(kit, "facts.yaml", {**GOOD_FACTS, "declarations": d})
    assert "R-GENAI" in ids(precheck.run(kit, rec, TODAY), "QUESTION")
    write(kit, "facts.yaml", {**GOOD_FACTS, "declarations": {**d, "genai": True}})
    f = [x for x in precheck.run(kit, rec, TODAY) if x.id == "R-GENAI"]
    assert {"BLOCKER", "LIKELY", "QUESTION", "INFO"} <= {x.severity for x in f}
    write(kit, "private/app-access.yaml", {"accounts": [{"user": "reviewer", "unlocks": "all AI features"}]})
    (kit / "private" / "genai-safety-tests.md").write_text("# tests\n")
    write(kit, "facts.yaml", {**GOOD_FACTS, "declarations": {**d, "genai": True},
                              "genai": {"reporting_entry_point": "Chat › ⋮ › Report", "ad_creatives_reviewed": True,
                                        "data_safety_reflects_prompts": True}})
    assert {x.severity for x in precheck.run(kit, rec, TODAY) if x.id == "R-GENAI"} == {"INFO"}


def test_account_rules(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0])
    write(kit, "facts.yaml", {**GOOD_FACTS, "account": {"type": "personal", "created_after_2023_11_13": True},
                              "declarations": {**GOOD_FACTS["declarations"], "financial": True}})
    f = precheck.run(kit, rec, TODAY)
    assert {"W-ORG", "W-TESTING-GATE"} <= ids(f, "BLOCKER")
    write(kit, "testing-evidence.yaml", {"decisions": [{"date": "2026-10-01", "outcome": "granted"}]})
    assert "W-TESTING-GATE" not in ids(precheck.run(kit, rec, TODAY))


def test_waivers_never_cover_artifact_blockers(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], target_sdk=33, dex=b"Lcom/google/android/gms/ads/AdView;")
    write(kit, "precheck-decisions.yaml", {"decisions": [
        {"rule": "B-TARGET", "reason": "later", "date": "2026-10-18", "decided_by": "owner"},
        {"rule": "R-ADID", "reason": "no ad id use; ads are contextual", "date": "2026-10-18", "decided_by": "owner"},
        {"rule": "R-DATASAFETY", "reason": "x"}]})
    f = precheck.run(kit, rec, TODAY)
    assert "B-TARGET" in ids(f, "BLOCKER") and any("waiver refused" in x.message for x in f)
    assert "R-ADID" not in ids(f) and any(x.id == "R-ADID" and x.waived for x in f)
    assert any("waiver ignored" in x.message for x in f if x.id == "R-DATASAFETY")


def test_dex_and_locale_warnings(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], dex=b"d" * (11 * 1024 * 1024))
    write(kit, "facts.yaml", {**GOOD_FACTS, "locales": ["en-US", "de-DE"]})
    f = precheck.run(kit, rec, TODAY)
    assert {"W-DEX", "W-LOCALE"} <= ids(f, "WARNING")
    rec.mapping_path = "mapping.txt"
    assert "W-DEX" not in ids(precheck.run(kit, rec, TODAY))


def test_tracked_keystore_blocks(tmp_path, kit, rsa_pem):
    import subprocess
    proj = tmp_path / "repo"
    proj.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=proj, check=True)
    (proj / "release.jks").write_bytes(b"k")
    subprocess.run(["git", "add", "release.jks"], cwd=proj, check=True)
    rec, _ = rec_for(tmp_path, rsa_pem[0])
    assert any("tracked in git" in x.message for x in precheck.run(kit, rec, TODAY, project=proj))


def test_debuggable_and_package_mismatch(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], debuggable=True, package="com.other")
    msgs = [x.message for x in precheck.run(kit, rec, TODAY) if x.id == "A-ARTIFACT"]
    assert len(msgs) == 2


def test_render_and_exit_codes(tmp_path, kit, rsa_pem):
    rec, _ = rec_for(tmp_path, rsa_pem[0], target_sdk=30)
    f = precheck.run(kit, rec, TODAY)
    md = precheck.render(rec, f, TODAY)
    assert md.startswith("# Pre-check — com.example.app") and "B-TARGET" in md
    assert any(x.counts() for x in f)
