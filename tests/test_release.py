"""release.py against FakePlay: gates before any request, apply → commit, conflicts, lost outcomes, transitions."""
import hashlib
import json

import pytest
import yaml

import approvals
import build
import play_api
import play_edit
import release
from play_edit import KitExit

PKG = "com.example.app"


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "play-store-kit"
    (k / "releases").mkdir(parents=True)
    (k / "facts.yaml").write_text(yaml.safe_dump({"package": PKG, "app_name": "Demo"}))
    (k / "console.yaml").write_text(yaml.safe_dump({"package": PKG, "publishing_mode": "standard",
                                                    "tracks": {"internal": None, "closed": {}, "open": None,
                                                               "production": None}}))
    return k


@pytest.fixture
def sa(sa_file):
    return play_api.load_service_account(sa_file)


def aab(tmp_path, code, name="1.0"):
    p = tmp_path / f"app-{code}.aab"
    p.write_bytes(f"PK fake bundle versionCode={code} name={name}".encode())
    return p


def inspector(path, **kw):
    data = open(path, "rb").read()
    code = int(data.split(b"versionCode=")[1].split()[0])
    return build.ArtifactRecord(path=str(path), sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
                                package=PKG, version_code=code, version_name="1.0", signer_sha256="ab" * 32,
                                mapping_path=kw.get("mapping"), native_symbols_path=kw.get("native_symbols"))


def runner(kit, client, sa, fake, precheck=lambda k, r: [], out=None):
    lines = out if out is not None else []
    return release.Runner(kit, client=client, sa=sa, now=fake.now, sleep=lambda s: None, inspect=inspector,
                          precheck=precheck, out=lines.append)


def imported(kit, client, sa, fake):
    r = runner(kit, client, sa, fake)
    r.cmd_import(write_kit=True)
    return runner(kit, client, sa, fake)


def receipt(kit, gate, text="yes, upload exactly that"):
    plan = play_edit.load_plan(kit)
    return str(approvals.record(kit, plan, gate, text))


def planned(kit, client, sa, fake, tmp_path, code=10, **kw):
    r = imported(kit, client, sa, fake)
    kw.setdefault("pending", ["none"])
    r.cmd_plan(str(aab(tmp_path, code)), kw.pop("track", "qa"), **kw)
    return runner(kit, client, sa, fake)


# ------------------------------------------------------------------------------------------------ import
def test_import_discovers_tracks_and_writes_console(kit, client, sa, fake):
    imported(kit, client, sa, fake)
    console = yaml.safe_load((kit / "console.yaml").read_text())
    assert console["tracks"]["production"] == "production" and console["tracks"]["internal"] == "qa"
    assert console["tracks"]["open"] == "beta" and console["tracks_imported_at"]
    live = json.loads((kit / "console-live.json").read_text())
    assert set(live["tracks"]) == {"production", "beta", "qa"} and live["view"] == "read from Play"
    assert fake.count("edits.delete") == 1        # the read-only edit is cleaned up
    assert not play_edit.Lock(kit, sa.fingerprint, PKG).read()


# ------------------------------------------------------------------------------------------------ gates
def test_plan_asks_and_sends_nothing(kit, client, sa, fake, tmp_path):
    out = []
    r = imported(kit, client, sa, fake)
    before = len(fake.calls)
    r.out = out.append
    plan = r.cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"], status="completed")
    assert len(fake.calls) == before
    assert plan["effects"] == ["G-UPLOAD", "G-TEST-DIST"]
    assert "[G-UPLOAD]" in out[0] and "[G-TEST-DIST]" in out[0]
    assert (kit / "play-plan.md").exists()


@pytest.mark.parametrize("case", ["none", "wrong_gate", "stale", "other_plan"])
def test_apply_without_matching_receipt_exits_2_with_zero_requests(kit, client, sa, fake, tmp_path, case):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    receipts = []
    if case == "wrong_gate":
        receipts = [receipt(kit, "G-TEST-DIST")]
    elif case == "stale":
        receipts = [receipt(kit, "G-UPLOAD")]
        plan = play_edit.load_plan(kit)
        plan["remote_baseline"]["tracks"]["qa"]["releases"] = [{"status": "draft"}]
        plan["fingerprint"] = approvals.fingerprint(plan)     # a re-made plan, same id
        play_edit.save_plan(kit, plan)
    elif case == "other_plan":
        good = receipt(kit, "G-UPLOAD")
        r2 = runner(kit, client, sa, fake)
        r2.cmd_plan(str(aab(tmp_path, 11)), "qa", ["none"])
        receipts = [good]
    before = len(fake.calls)
    with pytest.raises(KitExit) as e:
        r.cmd_apply(receipts)
    assert e.value.code == play_edit.EXIT_APPROVAL
    assert len(fake.calls) == before


def test_receipt_outside_approvals_dir_refused(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    good = receipt(kit, "G-UPLOAD")
    copy = tmp_path / "copied.json"
    copy.write_text(open(good).read())
    with pytest.raises(KitExit) as e:
        r.cmd_apply([str(copy)])
    assert e.value.code == 2


def test_plan_requires_pending_scope_and_known_mode(kit, client, sa, fake, tmp_path):
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit) as e:
        r.cmd_plan(str(aab(tmp_path, 10)), "qa", None)
    assert e.value.code == play_edit.EXIT_SCOPE
    c = yaml.safe_load((kit / "console.yaml").read_text())
    c["publishing_mode"] = "unknown"
    (kit / "console.yaml").write_text(yaml.safe_dump(c))
    with pytest.raises(KitExit) as e:
        runner(kit, client, sa, fake).cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"])
    assert e.value.code == play_edit.EXIT_SCOPE


def test_plan_refuses_blockers_and_staged_first_production(kit, client, sa, fake, tmp_path):
    finding = type("F", (), {"id": "B-TARGET", "severity": "BLOCKER", "message": "target too low",
                             "counts": lambda self, strict=False: True})()
    r = imported(kit, client, sa, fake)
    r.precheck = lambda k, rec: [finding]
    with pytest.raises(KitExit) as e:
        r.cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"])
    assert e.value.code == 1
    r.precheck = lambda k, rec: []
    with pytest.raises(KitExit, match="first production release"):
        r.cmd_plan(str(aab(tmp_path, 10)), "production", ["none"], status="inProgress", fraction=0.1)


def test_testing_gate_blocks_production_only(kit, client, sa, fake, tmp_path):
    gate = type("F", (), {"id": "W-TESTING-GATE", "severity": "BLOCKER", "message": "closed test",
                          "counts": lambda self, strict=False: True})()
    r = imported(kit, client, sa, fake)
    r.precheck = lambda k, rec: [gate]
    r.cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"])
    with pytest.raises(KitExit):
        r.cmd_plan(str(aab(tmp_path, 10)), "production", ["none"])


# ------------------------------------------------------------------------------------------------ apply / commit
def test_apply_then_commit(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    plan = play_edit.load_plan(kit)
    assert plan["state"] == "applied" and plan["progress"]["bundle"]["versionCode"] == 10
    assert fake.count("edits.commit") == 0
    with pytest.raises(KitExit) as e:                         # commit needs its own gate
        r.cmd_commit([receipt(kit, "G-UPLOAD")])
    assert e.value.code == 2 and fake.count("edits.commit") == 0
    r.cmd_commit([receipt(kit, "G-TEST-DIST", "yes, send it to testers")])
    assert play_edit.load_plan(kit)["state"] == "consumed"
    assert fake.apps[PKG].tracks["qa"]["releases"][0]["versionCodes"] == ["10"]
    hist = yaml.safe_load((kit / "releases" / "release-history.yaml").read_text())
    st = hist["releases"][0]["stages"]
    assert {"uploaded", "draft", "submitted"} <= set(st)
    commit_urls = [c[2] for c in fake.calls if c[0] == "edits.commit"]
    assert all("changesInReviewBehavior=ERROR_IF_IN_REVIEW" in u for u in commit_urls)


def test_draft_upload_only_needs_g_upload(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    plan = play_edit.load_plan(kit)
    assert plan["effects"] == ["G-UPLOAD"]
    up = receipt(kit, "G-UPLOAD")
    r.cmd_apply([up])
    r.cmd_commit([up])
    assert fake.apps[PKG].tracks["qa"]["releases"][0]["status"] == "draft"


def test_apply_keeps_other_releases(kit, client, sa, fake, tmp_path):
    fake.apps.clear()
    fake.add_app(PKG, tracks={"production": {"releases": [{"name": "1 (1)", "versionCodes": ["1"],
                                                           "status": "completed"}]},
                              "qa": {"releases": []}}, bundles=[{"versionCode": 1, "sha256": "x"}])
    r = planned(kit, client, sa, fake, tmp_path, track="production", status="inProgress", fraction=0.1)
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    r.cmd_commit([receipt(kit, "G-PROD-SUBMIT")])
    rel = fake.apps[PKG].tracks["production"]["releases"]
    assert [x["status"] for x in rel] == ["completed", "inProgress"]


def test_version_code_not_above_snapshot_refused(kit, client, sa, fake, tmp_path):
    fake.apps.clear()
    fake.add_app(PKG, tracks={"qa": {"releases": [{"versionCodes": ["20"], "status": "completed"}]}},
                 bundles=[{"versionCode": 20, "sha256": "x"}])
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit, match="bump"):
        r.cmd_plan(str(aab(tmp_path, 20)), "qa", ["none"])


def test_changed_aab_is_stale(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    up = receipt(kit, "G-UPLOAD")
    (tmp_path / "app-10.aab").write_bytes(b"PK changed versionCode=10")
    before = len(fake.calls)
    with pytest.raises(KitExit) as e:
        r.cmd_apply([up])
    assert e.value.code == play_edit.EXIT_STALE and len(fake.calls) == before


def test_baseline_drift_exits_7(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    fake.apps[PKG].tracks["qa"]["releases"] = [{"name": "x", "versionCodes": ["3"], "status": "draft"}]
    fake.apps[PKG].bundles.append({"versionCode": 3, "sha256": "y"})
    with pytest.raises(KitExit) as e:
        r.cmd_apply([receipt(kit, "G-UPLOAD")])
    assert e.value.code == play_edit.EXIT_GONE
    assert play_edit.load_plan(kit)["state"] == "invalidated"
    assert fake.count("edits.bundles.upload") == 0


def test_review_conflict_keeps_edit(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    fake.apps[PKG].review_open = True
    dist = receipt(kit, "G-TEST-DIST")
    with pytest.raises(KitExit) as e:
        r.cmd_commit([dist])
    assert e.value.code == play_edit.EXIT_REVIEW
    assert play_edit.load_plan(kit)["state"] == "applied"
    fake.finish_review(PKG)
    r.cmd_commit([dist])
    assert play_edit.load_plan(kit)["state"] == "consumed"
    assert fake.apps[PKG].cancelled_reviews == 0


def test_lost_commit_reconciles_to_succeeded(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    fake.fail("edits.commit", "lose_after")
    with pytest.raises(KitExit) as e:
        r.cmd_commit([receipt(kit, "G-TEST-DIST")])
    assert e.value.code == play_edit.EXIT_UNKNOWN
    r.cmd_reconcile()
    assert play_edit.load_plan(kit)["state"] == "consumed"
    assert fake.count("edits.commit") == 1


def test_lost_commit_that_did_not_land_stays_applied(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    fake.fail("edits.commit", "lose_before")
    dist = receipt(kit, "G-TEST-DIST")
    with pytest.raises(KitExit):
        r.cmd_commit([dist])
    r.cmd_reconcile()
    assert play_edit.load_plan(kit)["state"] == "applied"
    r.cmd_commit([dist])
    assert play_edit.load_plan(kit)["state"] == "consumed"


def test_lost_upload_reconciles_from_session_and_apply_resumes(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    up = receipt(kit, "G-UPLOAD")
    fake.fail("edits.bundles.upload#session", "lose_after", times=2)
    with pytest.raises(KitExit) as e:
        r.cmd_apply([up])
    assert e.value.code == play_edit.EXIT_UNKNOWN
    with pytest.raises(KitExit) as e:                          # nothing proceeds blind
        r.cmd_apply([up])
    assert e.value.code in (play_edit.EXIT_UNKNOWN, play_edit.EXIT_STALE)
    r.cmd_reconcile()
    plan = play_edit.load_plan(kit)
    assert plan["progress"]["bundle"]["versionCode"] == 10
    r.cmd_apply([up])
    assert play_edit.load_plan(kit)["state"] == "applied"
    assert len(fake.edits[plan["edit"]["id"]]["bundles"]) == 1


def test_lost_upload_with_expired_session_checks_bundles_list(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    up = receipt(kit, "G-UPLOAD")
    fake.fail("edits.bundles.upload#session", "lose_after", times=2)
    with pytest.raises(KitExit):
        r.cmd_apply([up])
    fake.expire_session()
    r.cmd_reconcile()
    assert play_edit.load_plan(kit)["progress"]["bundle"]["versionCode"] == 10
    assert fake.count("edits.bundles.list") == 1


def test_used_version_code_invalidates(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    fake.apps[PKG].bundles.append({"versionCode": 10, "sha256": "z"})   # used by a Console upload since
    with pytest.raises(KitExit, match="bump"):
        r.cmd_apply([receipt(kit, "G-UPLOAD")])
    assert play_edit.load_plan(kit)["state"] == "invalidated"


def test_console_change_between_apply_and_commit(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    fake.console_change(PKG)
    with pytest.raises(KitExit) as e:
        r.cmd_commit([receipt(kit, "G-TEST-DIST")])
    assert e.value.code == play_edit.EXIT_GONE
    assert play_edit.load_plan(kit)["state"] == "invalidated"


def test_new_plan_refused_while_one_is_applied(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    with pytest.raises(KitExit) as e:
        r.cmd_plan(str(aab(tmp_path, 11)), "qa", ["none"])
    assert e.value.code == play_edit.EXIT_STALE


def test_abort_deletes_edit(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    r.cmd_abort()
    assert play_edit.load_plan(kit)["state"] == "aborted" and fake.count("edits.delete") == 2


# ------------------------------------------------------------------------------------------------ status / wait
def test_status_is_edit_free_and_writes_releases_status(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path, status="completed")
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    r.cmd_commit([receipt(kit, "G-TEST-DIST")])
    inserts = fake.count("edits.insert")
    st = r.cmd_status()
    assert fake.count("edits.insert") == inserts
    assert st["qa"]["releases"][0]["versionCodes"] == ["10"]
    assert st["qa"]["releases"][0]["releaseLifecycleState"].endswith("IN_REVIEW")
    live = json.loads((kit / "console-live.json").read_text())
    assert live["releases_status"]["qa"]["checked_at"]
    fake.finish_review(PKG)
    assert r.cmd_wait(10, "qa", every=1, max_polls=3) == "PUBLISHED"
    r.cmd_status()
    hist = yaml.safe_load((kit / "releases" / "release-history.yaml").read_text())
    assert {"in_review", "published"} <= set(hist["releases"][0]["stages"])


# ------------------------------------------------------------------------------------------------ transitions
def staged_app(fake):
    fake.apps.clear()
    fake.add_app(PKG, tracks={"production": {"releases": [
        {"name": "1.0 (1)", "versionCodes": ["1"], "status": "completed"},
        {"name": "1.1 (2)", "versionCodes": ["2"], "status": "inProgress", "userFraction": 0.1}]},
        "qa": {"releases": [{"name": "1.2 (3)", "versionCodes": ["3"], "status": "completed"}]}},
        bundles=[{"versionCode": c, "sha256": str(c)} for c in (1, 2, 3)])


def test_rollout_two_step(kit, client, sa, fake):
    staged_app(fake)
    r = imported(kit, client, sa, fake)
    before = len(fake.calls)
    with pytest.raises(KitExit) as e:
        r._transition("rollout", "production", 2, "fraction", ["none"], "attested", None, fraction=0.5)
    assert e.value.code == 2 and len(fake.calls) == before
    plan = play_edit.load_plan(kit)
    assert plan["effects"] == ["G-ROLLOUT"]
    assert "keeps serving everyone else" in approvals.effect_sentence(plan, "G-ROLLOUT")
    r._transition("rollout", "production", 2, "fraction", None, "attested", [receipt(kit, "G-ROLLOUT")],
                  fraction=0.5)
    rel = fake.apps[PKG].tracks["production"]["releases"]
    assert rel[1]["userFraction"] == 0.5 and rel[0]["status"] == "completed"


def test_transition_receipt_for_other_parameters_is_stale(kit, client, sa, fake):
    staged_app(fake)
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit):
        r._transition("rollout", "production", 2, "fraction", ["none"], "attested", None, fraction=0.5)
    rec = receipt(kit, "G-ROLLOUT")
    before = len(fake.calls)
    with pytest.raises(KitExit) as e:
        r._transition("rollout", "production", 2, "fraction", None, "attested", [rec], fraction=0.9)
    assert e.value.code == play_edit.EXIT_STALE and len(fake.calls) == before


def test_halt_resume_complete(kit, client, sa, fake):
    staged_app(fake)
    for kind, kw in (("halt", {}), ("resume", {}), ("complete", {})):
        r = imported(kit, client, sa, fake)
        cmd = "rollout" if kind == "complete" else kind
        with pytest.raises(KitExit):
            r._transition(cmd, "production", 2, kind, ["none"], "attested", None, **kw)
        r._transition(cmd, "production", 2, kind, None, "attested", [receipt(kit, "G-ROLLOUT")], **kw)
        fake.finish_review(PKG)
    rel = fake.apps[PKG].tracks["production"]["releases"]
    assert rel == [{"name": "1.1 (2)", "versionCodes": ["2"], "status": "completed"}]


def test_promote_uses_prod_gate(kit, client, sa, fake):
    staged_app(fake)
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit):
        r._transition("promote", "production", 3, "promote", ["none"], "attested", None, source="qa",
                      status="inProgress", fraction=0.2)
    assert play_edit.load_plan(kit)["effects"] == ["G-PROD-SUBMIT"]
    r._transition("promote", "production", 3, "promote", None, "attested", [receipt(kit, "G-PROD-SUBMIT")],
                  source="qa", status="inProgress", fraction=0.2)
    rel = fake.apps[PKG].tracks["production"]["releases"]
    assert rel[-1] == {"name": "1.2 (3)", "versionCodes": ["3"], "status": "inProgress", "userFraction": 0.2}


def test_lost_transition_commit_reconciles(kit, client, sa, fake):
    staged_app(fake)
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit):
        r._transition("halt", "production", 2, "halt", ["none"], "attested", None)
    fake.fail("edits.commit", "lose_after")
    with pytest.raises(KitExit) as e:
        r._transition("halt", "production", 2, "halt", None, "attested", [receipt(kit, "G-ROLLOUT")])
    assert e.value.code == play_edit.EXIT_UNKNOWN
    r.cmd_reconcile()
    assert play_edit.load_plan(kit)["state"] == "consumed"


def test_tracks_create(kit, client, sa, fake):
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit) as e:
        r.cmd_tracks_create("friends", "default", ["none"], "attested", None)
    assert e.value.code == 2
    r.cmd_tracks_create("friends", "default", None, "attested", [receipt(kit, "G-META-PUB")])
    assert "friends" in fake.apps[PKG].tracks


@pytest.mark.parametrize("fn,args,msg", [
    (release.t_fraction, (2, 0.05), "only goes up"),
    (release.t_halt, (1,), "serving fallback"),
    (release.t_resume, (2,), "not halted"),
    (release.t_complete, (1,), "only an inProgress"),
])
def test_transform_refusals(fn, args, msg):
    rel = [{"versionCodes": ["1"], "status": "completed"},
           {"versionCodes": ["2"], "status": "inProgress", "userFraction": 0.1}]
    with pytest.raises(KitExit, match=msg):
        fn(rel, *args)


def test_halt_needs_fallback():
    with pytest.raises(KitExit, match="fall back"):
        release.t_halt([{"versionCodes": ["2"], "status": "inProgress", "userFraction": 0.1}], 2)


# ------------------------------------------------------------------------------------------------ CLI
def test_cli_dry_run_makes_no_requests(kit, capsys):
    assert release.main(["status", str(kit), "--dry-run"]) == 0
    assert "no network" in capsys.readouterr().out


def test_cli_maps_exit_codes(kit, client, sa, fake, tmp_path):
    r = planned(kit, client, sa, fake, tmp_path)
    assert release.main(["apply", str(kit)], runner=r) == 2


# ------------------------------------------------------------------------------------------------ draft apps
def test_draft_app_takes_only_draft_releases(kit, client, sa, fake, tmp_path):
    fake.add_app(PKG, draft=True)
    r = imported(kit, client, sa, fake)
    play_edit.update_yaml_keys(kit / "console.yaml", {"app_state": "draft"})
    r = runner(kit, client, sa, fake)
    before = len(fake.calls)
    with pytest.raises(KitExit) as e:
        r.cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"], status="completed")
    assert e.value.code == release.EXIT_FINDINGS and "draft app" in e.value.message
    assert len(fake.calls) == before
    r.cmd_plan(str(aab(tmp_path, 10)), "qa", ["none"], status="draft")
    r = runner(kit, client, sa, fake)
    r.cmd_apply([receipt(kit, "G-UPLOAD")])
    assert play_edit.load_plan(kit)["state"] == "applied"


def test_fake_refuses_a_completed_release_on_a_draft_app(kit, client, sa, fake, tmp_path):
    fake.add_app(PKG, draft=True)
    r = planned(kit, client, sa, fake, tmp_path, status="completed")     # app_state unknown to the kit
    with pytest.raises(play_api.PlayError) as e:
        r.cmd_apply([receipt(kit, "G-UPLOAD")])
    assert "stage the release as draft" in e.value.explain()


def test_import_keeps_console_comments_and_other_keys(kit, client, sa, fake):
    (kit / "console.yaml").write_text(
        "# my notes about this app\npackage: com.example.app\npublishing_mode: standard   # read 2026-10-19\n"
        "tracks:                 # ids from import\n  internal: null\n\nupload_certificate_sha256: AB:CD  # App "
        "integrity\nnotes: keep me\n")
    imported(kit, client, sa, fake)
    text = (kit / "console.yaml").read_text()
    assert text.startswith("# my notes about this app\n")
    assert "publishing_mode: standard   # read 2026-10-19" in text and "notes: keep me" in text
    assert "upload_certificate_sha256: AB:CD  # App integrity" in text
    assert "# ids from import" in text
    doc = yaml.safe_load(text)
    assert doc["tracks"]["internal"] == "qa" and doc["tracks_imported_at"]
