"""Phase 3: validate_kit, testing_evidence, console_guide, status_board (PLAN-FINAL §7, §8.6–§8.9, §3.5)."""
import datetime as dt
import json
import pathlib
import subprocess

import pytest
import yaml

import console_guide
import play_edit
import status_board
import testing_evidence
import validate_kit

D = dt.date


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "repo" / "play-store-kit"
    k.mkdir(parents=True)
    validate_kit.init(k)
    facts = yaml.safe_load((k / "facts.yaml").read_text())
    facts["package"] = "com.acme.notes"
    facts["account"].update(type="personal", created="2025-03-01")
    (k / "facts.yaml").write_text(yaml.safe_dump(facts))
    console = yaml.safe_load((k / "console.yaml").read_text())
    console["package"] = "com.acme.notes"
    (k / "console.yaml").write_text(yaml.safe_dump(console))
    return k


def _set(path, **kv):
    doc = yaml.safe_load(path.read_text()) or {}
    doc.update(kv)
    path.write_text(yaml.safe_dump(doc))


def _errors(kit, only=None):
    return [i["message"] for i in validate_kit.validate(kit, only).errors]


# ------------------------------------------------------------------------------------------------ validate_kit
def test_init_never_overwrites_and_templates_validate(kit):
    assert validate_kit.init(kit) == []
    assert (kit / "private" / "credentials.yaml").exists()
    assert _errors(kit, "schemas") == []


def test_package_and_enum_errors(kit):
    _set(kit / "facts.yaml", package="not a package", category_type="toy", target_devices=["fridge"])
    errs = " ".join(_errors(kit, "schemas"))
    assert "valid application id" in errs and "category_type" in errs and "fridge" in errs


def test_declaration_values_and_urls(kit):
    _set(kit / "facts.yaml", declarations={"ads": "maybe"}, privacy_policy_url="http://insecure.example")
    errs = " ".join(_errors(kit, "schemas"))
    assert "declarations.ads" in errs and "https://" in errs


def test_console_package_mismatch_and_cert_format(kit):
    _set(kit / "console.yaml", package="com.other", upload_certificate_sha256="abc",
         service_account_email="me@gmail.com")
    errs = " ".join(_errors(kit, "schemas"))
    assert "differs from facts.yaml" in errs and "32 hex bytes" in errs and "service account" in errs


def test_hand_typed_track_id_is_refused(kit):
    _set(kit / "console.yaml", tracks={"internal": "qa", "closed": {}, "open": None, "production": None})
    assert any("tracks_imported_at" in e for e in _errors(kit, "schemas"))
    _set(kit / "console.yaml", tracks_imported_at="2026-10-18T10:00:00Z")
    assert not any("tracks_imported_at" in e for e in _errors(kit, "schemas"))


def test_managed_track_must_be_discovered(kit):
    _set(kit / "console.yaml", tracks={"internal": "qa", "closed": {}, "production": "production"},
         tracks_imported_at="2026-10-18T10:00:00Z", managed={"listings": [], "images": {}, "tracks": ["beta"]})
    assert any("not a discovered track id" in e for e in _errors(kit, "schemas"))


def test_external_needs_output_glob(kit):
    _set(kit / "project.yaml", kind="external", output_glob=None)
    assert any("output_glob" in e for e in _errors(kit, "schemas"))


def test_incomplete_waiver_and_attested_extension(kit):
    (kit / "precheck-decisions.yaml").write_text(yaml.safe_dump({"decisions": [{"rule": "R-ADID"}]}))
    (kit / "policy-extensions.yaml").write_text(yaml.safe_dump({"extensions": [
        {"kind": "target_sdk", "until": "2026-11-01", "observed_at": "2026-10-18", "source": "attested"}]}))
    rep = validate_kit.validate(kit, "schemas")
    assert any("missing reason" in e["message"] for e in rep.errors)
    assert any("only source: console" in w["message"] for w in rep.items if w["level"] == "warning")


def test_scan_text_finds_secrets():
    assert validate_kit.scan_text("-----BEGIN PRIVATE KEY-----\nabc") == ["private key material"]
    assert "OAuth access token" in validate_kit.scan_text("token ya29.a0AfH6SMBxxxxxxxxxxxx")
    assert "resumable upload URL" in validate_kit.scan_text("https://x/upload?upload_id=ABCdef123456")
    assert "keystore password" in validate_kit.scan_text("storePassword=hunter2")
    assert validate_kit.scan_text("all clean, versionCode 42") == []
    with pytest.raises(ValueError, match="refusing to write"):
        validate_kit.assert_clean('{"private_key": "x"}', "STATUS.md")


def test_generated_file_with_token_fails(kit):
    (kit / "STATUS.md").write_text("Authorization: Bearer ya29.abcdefghijklmnop")
    assert any("STATUS.md" in i["where"] for i in validate_kit.validate(kit, "secrets").errors)


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def test_tracked_keystore_and_unignored_private_fail(kit):
    repo = kit.parent
    _git(repo, "init", "-q")
    (repo / "upload.jks").write_bytes(b"x")
    _git(repo, "add", "-f", "upload.jks")
    errs = " ".join(_errors(kit, "secrets"))
    assert "upload.jks is tracked" in errs and "is not ignored" in errs
    (repo / ".gitignore").write_text(validate_kit.GITIGNORE_SNIPPET.read_text())
    _git(repo, "rm", "-q", "--cached", "upload.jks")
    assert _errors(kit, "secrets") == []


def test_key_inside_repo_fails(kit):
    repo = kit.parent
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(validate_kit.GITIGNORE_SNIPPET.read_text())
    key = repo / "keys" / "play.json"
    key.parent.mkdir()
    key.write_text("{}")
    (kit / "private" / "credentials.yaml").write_text(yaml.safe_dump({"service_account_json": str(key)}))
    assert any("inside the repository" in e for e in _errors(kit, "secrets"))


# ------------------------------------------------------------------------------------------------ testing_evidence
def _obs(kit, *rows):
    for date, n, *src in rows:
        assert testing_evidence.main(["add", str(kit), "--date", date, "--testers-opted-in", str(n),
                                      "--source", src[0] if src else "console"]) == 0


def test_requirement_from_account_facts(kit):
    assert testing_evidence.requirement(kit)["applies"] is True
    _set(kit / "facts.yaml", account={"type": "personal", "created": "2023-11-13"})
    assert testing_evidence.requirement(kit)["applies"] is False     # on the date is not "after"
    _set(kit / "facts.yaml", account={"type": "organization"})
    assert testing_evidence.requirement(kit)["applies"] is False
    _set(kit / "facts.yaml", account={"type": "personal"})
    assert testing_evidence.requirement(kit)["applies"] is None


def test_run_needs_observation_on_day_14(kit):
    _obs(kit, ("2026-10-01", 12), ("2026-10-08", 14))
    st = testing_evidence.status(kit, D(2026, 10, 18))
    assert st["state"] == "counting" and st["earliest_application_day"] == "2026-10-15" and st["days_left"] == 0
    assert "record today's" in testing_evidence.render(st)
    _obs(kit, ("2026-10-15", 12))
    assert testing_evidence.status(kit, D(2026, 10, 18))["state"] == "ready_to_apply"


def test_drop_below_threshold_resets_the_run(kit):
    _obs(kit, ("2026-10-01", 12), ("2026-10-05", 11), ("2026-10-06", 12), ("2026-10-16", 12))
    st = testing_evidence.status(kit, D(2026, 10, 18))
    assert st["run_start"] == "2026-10-06" and st["earliest_application_day"] == "2026-10-20"
    assert st["state"] == "counting" and st["days_left"] == 2


def test_gaps_and_attested_only_are_flagged(kit):
    _obs(kit, ("2026-09-20", 12, "attested"), ("2026-10-05", 12, "attested"))
    st = testing_evidence.status(kit, D(2026, 10, 18))
    assert st["weak_gaps"] == ["2026-09-20 → 2026-10-05"] and st["attested_only"]
    assert st["state"] == "ready_to_apply"


def test_application_then_decision(kit):
    _obs(kit, ("2026-10-01", 12), ("2026-10-16", 12))
    testing_evidence.main(["apply-recorded", str(kit), "--date", "2026-10-16"])
    assert testing_evidence.status(kit, D(2026, 10, 18))["state"] == "applied_waiting"
    testing_evidence.main(["decision", str(kit), "--date", "2026-10-17", "--outcome", "denied", "--reasons", "x"])
    assert testing_evidence.status(kit, D(2026, 10, 18))["state"] == "ready_to_apply"
    testing_evidence.main(["decision", str(kit), "--date", "2026-10-18", "--outcome", "granted"])
    assert testing_evidence.status(kit, D(2026, 10, 18))["state"] == "granted"


def test_future_date_refused(kit):
    future = str(dt.date.today() + dt.timedelta(days=5))
    assert testing_evidence.main(["add", str(kit), "--date", future, "--testers-opted-in", "12",
                                  "--source", "console"]) == 1


def test_precheck_reads_the_granted_decision(kit):
    testing_evidence.main(["decision", str(kit), "--date", "2026-10-01", "--outcome", "granted"])
    doc = yaml.safe_load((kit / "testing-evidence.yaml").read_text())
    assert any(d["outcome"] == "granted" for d in doc["decisions"])   # the key precheck W-TESTING-GATE reads


# ------------------------------------------------------------------------------------------------ console_guide
def test_guide_carries_values_and_unknowns(kit):
    _set(kit / "facts.yaml", app_name="Acme Notes", declarations={"financial": True, "account_creation": True})
    text = console_guide.render_section(kit, "bootstrap")
    assert "Acme Notes" in text and "com.acme.notes" in text
    assert "need an organization account" in text and "Account deletion URL" in text
    assert "❓" in text and "answer/9859455" in text


def test_every_step_has_a_help_url_and_owner():
    for s in console_guide.steps({}, {}):
        assert s["url"].startswith("https://") and s["who"] in ("owner", "kit") and s["section"] in console_guide.SECTIONS


def test_observed_needs_a_console_url(kit):
    with pytest.raises(SystemExit, match="observed needs --url"):
        console_guide.mark(kit, "upload-cert", "observed")
    console_guide.mark(kit, "upload-cert", "observed", url="https://play.google.com/console/u/0/developers/1/app/2/keymanagement")
    assert console_guide.load_progress(kit)["upload-cert"]["state"] == "observed"
    with pytest.raises(SystemExit, match="unknown step"):
        console_guide.mark(kit, "nope", "attested")


def test_render_writes_guide_and_private_html_escaped(kit):
    (kit / "private" / "app-access.yaml").write_text(yaml.safe_dump(
        {"accounts": [{"label": "AI full access", "username": "rev@acme.test", "password": "<pw&>"}]}))
    assert console_guide.main([str(kit), "render", "--private"]) == 0
    md = (kit / "CONSOLE-GUIDE.md").read_text()
    assert "rev@acme.test" not in md and "## Bootstrap" in md
    html = (kit / "private" / "console-guide.private.html").read_text()
    assert "&lt;pw&amp;&gt;" in html


def test_production_access_draft_uses_evidence(kit):
    _obs(kit, ("2026-10-01", 12), ("2026-10-16", 15))
    text = console_guide.render_section(kit, "production-access")
    assert "15 opted in" in text and "since 2026-10-01" in text and "‹" in text


# ------------------------------------------------------------------------------------------------ status_board
def _board(kit, today=D(2026, 10, 18)):
    return status_board.collect(kit, today)


def test_board_fresh_kit(kit):
    c = _board(kit)
    rows = {n: i for i, n, _ in status_board.journey_rows(c)}
    assert rows["J0 Connect"] == "⬜" and rows["J1 Bootstrap"] == "🌐" and rows["J6 Testing gate"] == "🔴"
    text = status_board.render(c)
    assert "## Deadlines" in text and "Target API 36" in text and "in force" in text


def test_board_attested_is_never_green(kit):
    console_guide.mark(kit, "first-upload", "attested")
    rows = {n: i for i, n, _ in status_board.journey_rows(_board(kit))}
    assert rows["J1 Bootstrap"] == "✋"


def test_board_connect_and_lifecycle(kit):
    (kit / "console-live.json").write_text(json.dumps({
        "capabilities": {"publisher_api": "ok", "app_access": "ok", "checked_at": "2026-10-18T09:00:00Z"},
        "releases_status": {"production": {"checked_at": "2026-10-18T09:05:00Z", "releases": [
            {"versionCodes": ["42"], "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW"},
            {"versionCodes": ["41"], "releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED"}]}}}))
    (kit / "releases" / "release-history.yaml").write_text(yaml.safe_dump({"releases": [
        {"version_code": 41, "track": "production", "stages": {}},
        {"version_code": 42, "track": "production", "stages": {"submitted": "2026-10-18T08:00:00Z"}}]}))
    c = _board(kit)
    assert {n: i for i, n, _ in status_board.journey_rows(c)}["J0 Connect"] == "✅"
    rel = {r["version_code"]: r for r in status_board.release_rows(c)}
    assert rel["42"]["icon"] == "⏸" and rel["42"]["state"] == "in review"
    assert rel["41"]["icon"] == "⏸" and "serving not observed" in rel["41"]["state"]   # published ≠ done


def test_board_lists_unknown_outcomes(kit):
    j = play_edit.Journal(kit / "private" / "journal" / "p1.jsonl")
    j.append(op="commit", phase="started", method="edits.commit")
    j.append(op="commit", phase="unknown", method="edits.commit", reason="timeout")
    text = status_board.render(_board(kit))
    assert "## ❓ Needs reconcile" in text and "edits.commit" in text


def test_board_refuses_to_write_secrets(kit, monkeypatch):
    monkeypatch.setattr(status_board, "render", lambda c: "ya29.abcdefghijklmnopqrstu")
    with pytest.raises(ValueError):
        status_board.main([str(kit), "--today", "2026-10-18"])
    assert not (kit / "STATUS.md").exists()


# ------------------------------------------------------------------------------------------------ 1.0.1 fixes
def test_relative_kit_path_checks_the_right_private(kit, monkeypatch):
    repo = kit.parent
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text(validate_kit.GITIGNORE_SNIPPET.read_text())
    monkeypatch.chdir(repo)
    assert _errors(pathlib.Path("play-store-kit"), "secrets") == []
    assert validate_kit.main(["play-store-kit", "--only", "secrets"]) == 0


def test_init_adds_gitignore_lines_and_keeps_templates_out(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / "android").mkdir(parents=True)
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("build/")              # no trailing newline
    kit = repo / "android" / "play-store-kit"
    kit.mkdir()
    (kit / "gitignore.snippet").write_text(validate_kit.GITIGNORE_SNIPPET.read_text())   # a hand copy
    assert validate_kit.main([str(kit), "--init"]) == 0
    text = (repo / ".gitignore").read_text()
    assert text.startswith("build/\n") and "android/play-store-kit/private/" in text
    assert not (kit / "gitignore.snippet").exists() and not (kit / "credentials.example.yaml").exists()
    assert (kit / "private" / "credentials.yaml").exists()
    assert _errors(kit, "secrets") == []
    validate_kit.main([str(kit), "--init"])                   # second run adds nothing
    assert (repo / ".gitignore").read_text() == text
    assert "already ignores" in capsys.readouterr().out


def test_init_outside_git_prints_lines(tmp_path, capsys):
    kit = tmp_path / "plain" / "play-store-kit"
    validate_kit.main([str(kit), "--init"])
    out = capsys.readouterr().out
    assert "not a git repository" in out and "play-store-kit/private/" in out
    assert not (tmp_path / "plain" / ".gitignore").exists()


def test_board_shows_extension_ends_only_when_recorded(kit):
    labels = [r[2] for r in status_board.deadline_rows(_board(kit))]
    assert "Target API extension ends" not in labels and "Billing Library extension ends" not in labels
    assert "Billing Library 9 required" in labels
    _set(kit / "policy-extensions.yaml", extensions=[{"kind": "billing_library", "until": "2026-11-01",
                                                      "observed_at": "2026-10-18", "source": "console"}])
    labels = [r[2] for r in status_board.deadline_rows(_board(kit))]
    assert "Billing Library extension ends" in labels and "Target API extension ends" not in labels


def test_guide_app_access_follows_facts():
    no_accounts = {"declarations": {"account_creation": False, "digital_goods": False}}
    assert "All functionality is available" in console_guide.app_access_text(no_accounts)
    gate = {"declarations": {"account_creation": False}, "app_access": {"instructions": "hold the grown-up button"}}
    assert "restricted" in console_guide.app_access_text(gate) and "grown-up" in console_guide.app_access_text(gate)
    paid = {"declarations": {"account_creation": False, "digital_goods": True}}
    t = console_guide.app_access_text(paid)
    assert "promo code" in t and "500 characters" in t and "partner devices" in t
    assert "sign-in" in console_guide.app_access_text({"declarations": {"account_creation": True}})


def test_guide_steps_for_a_paid_childrens_app():
    facts = {"package": "com.acme.kids", "declarations": {"digital_goods": True, "children_audience": True}}
    console = {"developer_account_id": "123", "console_user_index": 4, "app_id": "987"}
    pre = {"aab": "/b/app.aab", "signer_sha256": "a452ec" + "00" * 29,
           "permission_declarations": ["Foreground service types declaration"]}
    st = {s["id"]: s["do"] for s in console_guide.steps(facts, console, pre)}
    ids = list(st)
    assert ids.index("payments-profile") < ids.index("in-app-products") < ids.index("promo-codes")
    assert ids.index("first-upload") < ids.index("in-app-products")
    assert "store-listing" in st and "Foreground service types declaration" in st["app-content"]
    assert "COPPA" in st["target-audience"] and "Teacher Approved" in st["target-audience"]
    assert "A4:52:EC:" in st["upload-cert"] and "stop" in st["upload-cert"]
    assert 'open -R "/b/app.aab"' in st["first-upload"] and "Releases are signed by Google Play" in st["first-upload"]
    assert "accept Play App Signing" not in st["first-upload"]
    assert "Check availability" in st["create-app"] and "com.acme.kids" in st["create-app"]
    assert "Edit and delete draft apps" in st["invite"] and "View financial data" in st["invite"]
    assert "https://play.google.com/console/u/4/developers/123/users-and-permissions" in st["invite"]
    assert "u/4/developers/123/app/987/app-dashboard" in st["testing-gate"]


def test_free_app_has_no_product_steps():
    ids = {s["id"] for s in console_guide.steps({"declarations": {"digital_goods": False}}, {})}
    assert not ids & {"payments-profile", "in-app-products", "promo-codes"}
    assert {"developer-account", "testing-gate", "send-for-review"} <= ids


def test_mark_accepts_conditional_steps(kit):
    console_guide.mark(kit, "promo-codes", "attested")
    assert console_guide.load_progress(kit)["promo-codes"]["state"] == "attested"
