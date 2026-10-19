"""Facts the kit relies on, asserted against Google's pinned discovery document (rev 20261015).

When Google changes the API, refresh the fixture and these tests say what moved.
"""
from conftest import method


def test_revision_pinned(discovery):
    assert discovery["revision"] == "20261015"
    assert discovery["rootUrl"] == "https://androidpublisher.googleapis.com/"


def test_bundle_upload_paths(discovery):
    m = method(discovery, "edits.bundles.upload")
    assert m["httpMethod"] == "POST"
    protocols = m["mediaUpload"]["protocols"]
    assert protocols["simple"]["path"] == "/upload/androidpublisher/v3/applications/{packageName}/edits/{editId}/bundles"
    assert protocols["resumable"]["path"] == "/resumable/upload/androidpublisher/v3/applications/{packageName}/edits/{editId}/bundles"
    assert "application/octet-stream" in m["mediaUpload"]["accept"]


def test_commit_review_behaviour(discovery):
    m = method(discovery, "edits.commit")
    assert m["path"] == "androidpublisher/v3/applications/{packageName}/edits/{editId}:commit"
    p = m["parameters"]
    assert "ERROR_IF_IN_REVIEW" in p["changesInReviewBehavior"]["enum"]
    # The server default cancels a review in progress: the kit must always send ERROR_IF_IN_REVIEW.
    assert "CANCEL_IN_REVIEW_AND_SUBMIT" in p["changesInReviewBehavior"]["description"]
    assert p["changesNotSentForReview"]["type"] == "boolean"


def test_edit_free_release_status(discovery):
    m = method(discovery, "applications.tracks.releases.list")
    assert m["httpMethod"] == "GET"
    assert m["path"] == "androidpublisher/v3/{+parent}/releases"
    states = discovery["schemas"]["ReleaseSummary"]["properties"]["releaseLifecycleState"]["enum"]
    for s in ("DRAFT", "NOT_SENT_FOR_REVIEW", "IN_REVIEW", "APPROVED_NOT_PUBLISHED", "NOT_APPROVED", "PUBLISHED"):
        assert f"RELEASE_LIFECYCLE_STATE_{s}" in states  # values carry this prefix


def test_edit_lifecycle_methods(discovery):
    edits = discovery["resources"]["edits"]
    assert set(edits["methods"]) >= {"insert", "get", "validate", "commit", "delete"}
    assert set(edits["resources"]["tracks"]["methods"]) >= {"get", "list", "patch", "update", "create"}
    app_edit = discovery["schemas"]["AppEdit"]["properties"]
    assert {"id", "expiryTimeSeconds"} <= set(app_edit)


def test_track_release_status_values(discovery):
    status = discovery["schemas"]["TrackRelease"]["properties"]["status"]["enum"]
    for s in ("draft", "inProgress", "halted", "completed"):
        assert s in status


def _norm(path):
    import re
    return re.sub(r"{\+?\w+}", "{}", path)


def test_every_kit_method_is_in_the_discovery_document(discovery):
    import play_api

    for mid, (verb, template, klass, protocols) in play_api.CONTRACT.items():
        m = method(discovery, mid)
        assert m["httpMethod"] == verb, mid
        doc_paths = {_norm(m["path"]), _norm(m.get("flatPath", m["path"]))}
        assert _norm("androidpublisher/v3/" + template) in doc_paths, mid
        for proto in protocols or ():
            assert _norm(m["mediaUpload"]["protocols"][proto]["path"]) == \
                _norm("/" + {"simple": "upload/", "resumable": "resumable/upload/"}[proto]
                      + "androidpublisher/v3/" + template), (mid, proto)
        for q in play_api.QUERY.get(mid, ()):
            assert q in m.get("parameters", {}) or q in discovery["parameters"], (mid, q)
        assert klass in ("read", "idempotent", "reconcilable", "destructive")


def test_client_urls_match_discovery(discovery):
    import play_api

    c = play_api.Client(None, transport=None)
    url = c.url("edits.bundles.upload", {"packageName": "p", "editId": "e"}, {"uploadType": "resumable"},
                protocol="resumable")
    assert url == "https://androidpublisher.googleapis.com/resumable/upload/androidpublisher/v3/applications/p/edits/e/bundles?uploadType=resumable"


def test_store_presence_methods(discovery):
    edits = discovery["resources"]["edits"]["resources"]
    assert {"update", "patch", "list", "get"} <= set(edits["listings"]["methods"])
    assert {"update", "get"} <= set(edits["details"]["methods"])
    assert {"update", "get"} <= set(edits["testers"]["methods"])
    up = edits["images"]["methods"]["upload"]
    assert "image/*" in up["mediaUpload"]["accept"]
    types = up["parameters"]["imageType"]["enum"]
    for t in ("icon", "featureGraphic", "phoneScreenshots", "sevenInchScreenshots", "tenInchScreenshots"):
        assert t in types
    assert "deleteall" in edits["images"]["methods"]
    # Country availability can only be read through the API; countries are set in the Console.
    assert set(edits["countryavailability"]["methods"]) == {"get"}


def test_one_time_products_use_the_monetization_api(discovery):
    import play_api

    otp = discovery["resources"]["monetization"]["resources"]["onetimeproducts"]["methods"]
    assert otp["batchUpdate"]["flatPath"].endswith("/oneTimeProducts:batchUpdate")
    # camelCase everywhere except patch, whose flatPath is lowercase
    assert "/onetimeproducts/" in otp["patch"]["flatPath"]
    assert "convertRegionPrices" in discovery["resources"]["monetization"]["methods"]
    assert not any(mid.startswith("inappproducts.") for mid in play_api.CONTRACT)
