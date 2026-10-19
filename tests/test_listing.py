"""listing.py against FakePlay: lint, probe in throwaway edits, G-META-PUB before any write, apply → commit."""
import json

import pytest
import yaml

import approvals
import listing
import play_api
import play_edit
from play_edit import KitExit

PKG = "com.example.app"
EN = {"en-US": {"language": "en-US", "title": "Kids Love Science", "shortDescription": "Read-aloud science",
                "fullDescription": "Stories."}}


@pytest.fixture
def kit(tmp_path):
    k = tmp_path / "play-store-kit"
    (k / "releases").mkdir(parents=True)
    (k / "facts.yaml").write_text(yaml.safe_dump({"package": PKG, "app_name": "Demo"}))
    (k / "console.yaml").write_text(yaml.safe_dump({"package": PKG, "publishing_mode": "standard"}))
    return k


@pytest.fixture
def sa(sa_file):
    return play_api.load_service_account(sa_file)


def runner(kit, client, sa, fake, out=None):
    return listing.ListingRunner(kit, client=client, sa=sa, now=fake.now, sleep=lambda s: None,
                                 out=(out if out is not None else []).append)


def imported(kit, client, sa, fake):
    runner(kit, client, sa, fake).cmd_import(write_kit=True)
    return runner(kit, client, sa, fake)


def receipt(kit, text="yes, publish that listing"):
    return str(approvals.record(kit, play_edit.load_plan(kit), "G-META-PUB", text))


def test_lint_uses_rule_limits(kit):
    bad = {"en-US": {"title": "x" * 31, "shortDescription": "", "fullDescription": "ok", "color": "red"}}
    problems = listing.lint(kit, bad, {"en-US": {"icon": ["a.png", "b.png"], "banner": []}})
    text = "\n".join(problems)
    assert "title has 31 characters, the limit is 30" in text and "shortDescription is empty" in text
    assert "color is not a Listing field" in text and "takes exactly one image" in text
    assert "'banner' is not one of" in text and "a.png does not exist" in text
    assert listing.lint(kit, EN) == []


def test_probe_tells_permission_from_values_and_leaves_nothing(kit, client, sa, fake):
    fake.add_app(PKG, draft=True)
    fake.apps[PKG].draft_permission = False
    r = imported(kit, client, sa, fake)
    res = r.cmd_probe(EN, {"contactEmail": "hi@example.com"})
    assert res["empty edit"] == "ok"
    assert res["listings"].startswith("refused: HTTP 403") and "Edit and delete draft apps" in res["listings"]
    assert fake.count("edits.commit") == 0
    assert all(e["state"] == "deleted" for e in fake.edits.values())
    assert fake.apps[PKG].listings == {}
    fake.apps[PKG].draft_permission = True
    assert set(runner(kit, client, sa, fake).cmd_probe(EN, {}).values()) == {"ok"}


def test_plan_asks_first_and_sends_nothing(kit, client, sa, fake):
    r = imported(kit, client, sa, fake)
    before = len(fake.calls)
    out = []
    r = runner(kit, client, sa, fake, out)
    with pytest.raises(KitExit) as e:
        r.cmd_plan(EN, {}, {}, ["none"])
    assert e.value.code == play_edit.EXIT_APPROVAL and len(fake.calls) == before
    assert "[G-META-PUB] Store presence changes go live" in out[0] and "new listing en-US" in out[0]
    assert "## Changes" in (kit / "play-plan.md").read_text()
    with pytest.raises(KitExit) as e:
        runner(kit, client, sa, fake).cmd_apply([])
    assert e.value.code == play_edit.EXIT_APPROVAL and len(fake.calls) == before


def test_apply_commit_texts_and_images(kit, client, sa, fake):
    (kit / "graphics").mkdir()
    (kit / "graphics" / "icon.png").write_bytes(b"\x89PNG icon")
    (kit / "graphics" / "s1.png").write_bytes(b"\x89PNG one")
    (kit / "graphics" / "s2.png").write_bytes(b"\x89PNG two")
    images = {"en-US": {"icon": ["graphics/icon.png"], "phoneScreenshots": ["graphics/s1.png", "graphics/s2.png"]}}
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit):
        r.cmd_plan(EN, {"contactEmail": "hi@example.com"}, images, ["none"])
    rec = receipt(kit)
    r = runner(kit, client, sa, fake)
    r.cmd_apply([rec])
    assert fake.apps[PKG].listings == {}                 # nothing is live before the commit
    r = runner(kit, client, sa, fake)
    r.cmd_commit([rec])
    app = fake.apps[PKG]
    assert app.listings["en-US"]["title"] == "Kids Love Science"
    assert app.details["contactEmail"] == "hi@example.com"
    assert len(app.images[("en-US", "phoneScreenshots")]) == 2 and len(app.images[("en-US", "icon")]) == 1
    assert play_edit.load_plan(kit)["state"] == "consumed"
    commit = [c for c in fake.calls if c[0] == "edits.commit"][0]
    assert "ERROR_IF_IN_REVIEW" in commit[2]


def test_unchanged_listing_is_not_planned(kit, client, sa, fake):
    fake.apps[PKG].listings = {"en-US": dict(EN["en-US"])}
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit) as e:
        r.cmd_plan(EN, {}, {}, ["none"])
    assert e.value.code == play_edit.EXIT_OK and "already matches" in e.value.message


def test_console_change_after_plan_is_caught(kit, client, sa, fake):
    r = imported(kit, client, sa, fake)
    with pytest.raises(KitExit):
        r.cmd_plan(EN, {}, {}, ["none"])
    rec = receipt(kit)
    fake.apps[PKG].listings = {"de-DE": {"language": "de-DE", "title": "Neu"}}   # someone saved in the Console
    with pytest.raises(KitExit) as e:
        runner(kit, client, sa, fake).cmd_apply([rec])
    assert e.value.code == play_edit.EXIT_GONE
    assert fake.count("edits.listings.update") == 0


def test_cli_lint(kit):
    (kit / "listings.json").write_text(json.dumps(EN))
    assert listing.main(["lint", str(kit)]) == 0
    (kit / "listings.json").write_text(json.dumps({"en-US": {"title": "x" * 40, "shortDescription": "s"}}))
    assert listing.main(["lint", str(kit)]) == 1
