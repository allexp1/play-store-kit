"""§15.2 stateful sequences: one edit owner, a lock, a persisted edit, no edit inserted for status."""
import json
import multiprocessing

import pytest

import play_api
import play_edit
from play_edit import EditOwner, KitExit

PKG = "com.example.app"


def make_plan(kit, sa, plan_id="p1", state="approved"):
    plan = {"schema": 1, "plan_id": plan_id, "state": state,
            "principal": {"client_email": sa.client_email, "fingerprint": sa.fingerprint},
            "app": {"package": PKG}, "edit": {"id": None, "expiry_time_seconds": None},
            "journal": f"private/journal/{plan_id}.jsonl"}
    play_edit.save_plan(kit, plan)
    return plan


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "play-store-kit"
    k.mkdir()
    return k


@pytest.fixture
def sa(sa_file):
    return play_api.load_service_account(sa_file)


@pytest.fixture
def owner(client, kit, fake):
    return EditOwner(client, kit, now=fake.now)


def applied(owner, kit, sa, plan_id="p1"):
    plan = make_plan(kit, sa, plan_id)
    owner.open(plan)
    plan["state"] = "applied"
    play_edit.save_plan(kit, plan)
    return plan


def test_open_persists_edit_before_other_writes(owner, kit, sa, fake):
    plan = make_plan(kit, sa)
    edit = owner.open(plan)
    on_disk = play_edit.load_plan(kit)
    assert on_disk["edit"]["id"] == edit["id"] and on_disk["edit"]["expiry_time_seconds"] > fake.now()
    lock = play_edit.Lock(kit, sa.fingerprint, PKG).read()
    assert lock["plan_id"] == "p1" and lock["edit_id"] == edit["id"]


def test_apply_status_commit_keeps_edit_a(owner, client, kit, sa, fake):
    plan = applied(owner, kit, sa)
    a = plan["edit"]["id"]
    client.releases(PKG, "production")          # status is edit-free
    owner.commit(plan)
    assert fake.count("edits.insert") == 1
    assert fake.edits[a]["state"] == "committed" and plan["state"] == "consumed"
    assert play_edit.Lock(kit, sa.fingerprint, PKG).read() is None


def test_plan_is_single_use(owner, kit, sa):
    plan = applied(owner, kit, sa)
    owner.commit(plan)
    with pytest.raises(KitExit) as e:
        owner.commit(plan)
    assert e.value.code == play_edit.EXIT_STALE


def test_readonly_edit_reuses_persisted_edit(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    with owner.readonly_edit(plan) as (eid, reused):
        assert reused and eid == plan["edit"]["id"]
    assert fake.count("edits.insert") == 1 and fake.edits[eid]["state"] == "open"


def test_readonly_edit_without_plan_inserts_and_deletes(owner, kit, sa, fake):
    snap = {"plan_id": "import-1", "app": {"package": PKG},
            "principal": {"fingerprint": sa.fingerprint}, "edit": {}}
    with owner.readonly_edit(snap) as (eid, reused):
        assert not reused
    assert fake.edits[eid]["state"] == "deleted"
    assert play_edit.Lock(kit, sa.fingerprint, PKG).read() is None


def test_second_plan_gets_lock_exit_6(owner, kit, sa):
    applied(owner, kit, sa, "p1")
    other = make_plan(kit, sa, "p2")
    with pytest.raises(KitExit) as e:
        owner.open(other)
    assert e.value.code == play_edit.EXIT_LOCK and e.value.detail["plan_id"] == "p1"


def _grab(path, q):
    import sys
    sys.path.insert(0, str(path))
    import play_edit as pe
    try:
        pe.Lock(q[0], q[1], PKG).acquire("other-process")
        q[2].put("got it")
    except pe.KitExit as e:
        q[2].put(e.code)


def test_second_local_process_gets_exit_6(owner, kit, sa):
    from conftest import ROOT
    applied(owner, kit, sa)
    ctx = multiprocessing.get_context("spawn")
    out = ctx.Queue()
    p = ctx.Process(target=_grab, args=(ROOT / "scripts", (str(kit), sa.fingerprint, out)))
    p.start()
    p.join(30)
    assert out.get(timeout=5) == play_edit.EXIT_LOCK


def test_other_user_commit_invalidates_no_delete(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.other_user_commit(PKG)
    with pytest.raises(KitExit) as e:
        owner.commit(plan)
    assert e.value.code == play_edit.EXIT_GONE
    assert play_edit.load_plan(kit)["state"] == "invalidated"
    assert fake.count("edits.delete") == 0 and fake.count("edits.commit") == 1


def test_console_change_fails_validate(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.console_change(PKG)
    with pytest.raises(KitExit) as e:
        owner.validate(plan)
    assert e.value.code == play_edit.EXIT_GONE and play_edit.load_plan(kit)["state"] == "invalidated"
    assert fake.count("edits.delete") == 0


def test_expiry_resume_exit_7(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.advance(fake.edit_lifetime + 1)
    with pytest.raises(KitExit) as e:
        owner.resume(plan)
    assert e.value.code == play_edit.EXIT_GONE


def test_restart_resume_finds_persisted_edit(client, kit, sa, fake):
    first = EditOwner(client, kit, now=fake.now)
    plan = applied(first, kit, sa)
    again = EditOwner(client, kit, now=fake.now)       # a new process
    assert again.resume(play_edit.load_plan(kit))["id"] == plan["edit"]["id"]
    assert fake.count("edits.insert") == 1


def test_superseded_edit_is_gone(owner, client, kit, sa, fake):
    plan = applied(owner, kit, sa)
    client.call("edits.insert", {"packageName": PKG}, body={})   # e.g. CI with the same principal
    with pytest.raises(KitExit) as e:
        owner.resume(plan)
    assert e.value.code == play_edit.EXIT_GONE


def test_review_conflict_keeps_edit_exit_4(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.apps[PKG].review_open = True
    with pytest.raises(KitExit) as e:
        owner.commit(plan)
    assert e.value.code == play_edit.EXIT_REVIEW
    assert play_edit.load_plan(kit)["state"] == "applied" and fake.edits[plan["edit"]["id"]]["state"] == "open"
    assert fake.apps[PKG].cancelled_reviews == 0
    fake.finish_review(PKG)
    owner.commit(plan)
    assert plan["state"] == "consumed"


def test_unknown_commit_blocks_abort_until_reconcile(owner, client, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.fail("edits.commit", "lose_after")
    with pytest.raises(KitExit) as e:
        owner.commit(plan)
    assert e.value.code == play_edit.EXIT_UNKNOWN and play_edit.load_plan(kit)["state"] == "unknown"
    with pytest.raises(KitExit) as e:
        owner.abort(plan)
    assert e.value.code == play_edit.EXIT_UNKNOWN and fake.count("edits.delete") == 0
    assert owner.reconcile_commit(plan, landed=lambda: True) == "succeeded"
    assert play_edit.load_plan(kit)["state"] == "consumed"
    assert fake.count("edits.commit") == 1          # never replayed


def test_unknown_commit_that_did_not_land(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    fake.fail("edits.commit", "lose_before")
    with pytest.raises(KitExit):
        owner.commit(plan)
    assert owner.reconcile_commit(plan, landed=lambda: False) == "applied"
    assert play_edit.load_plan(kit)["state"] == "applied"
    owner.commit(plan)
    assert fake.count("edits.commit") == 2 and plan["state"] == "consumed"


def test_abort_deletes_and_releases(owner, kit, sa, fake):
    plan = applied(owner, kit, sa)
    owner.abort(plan)
    assert fake.edits[plan["edit"]["id"]]["state"] == "deleted" and plan["state"] == "aborted"
    assert play_edit.Lock(kit, sa.fingerprint, PKG).read() is None


def test_lost_insert_is_safe(owner, kit, sa, fake):
    plan = make_plan(kit, sa)
    fake.fail("edits.insert", "lose_after")
    with pytest.raises(KitExit) as e:
        owner.open(plan)
    assert e.value.code == play_edit.EXIT_UNKNOWN
    owner.open(plan)                                 # the next insert supersedes the orphan
    orphans = [x for x in fake.edits.values() if x["state"] == "superseded"]
    assert len(orphans) == 1


def test_journal_lines_and_redaction(owner, kit, sa):
    plan = applied(owner, kit, sa)
    owner.commit(plan)
    lines = [json.loads(l) for l in (kit / plan["journal"]).read_text().splitlines()]
    assert [l["phase"] for l in lines] == ["started", "succeeded", "started", "succeeded"]
    assert [l["seq"] for l in lines] == [1, 2, 3, 4]
    assert "@" not in (kit / plan["journal"]).read_text()


def test_show_needs_no_network(owner, kit, sa):
    applied(owner, kit, sa)
    out = play_edit.show(kit)
    assert out["lock"]["plan_id"] == "p1" and out["unresolved"] == []
