"""Shared test setup: scripts/ on sys.path and the pinned discovery document."""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

FIXTURES = ROOT / "tests" / "fixtures"
DISCOVERY = FIXTURES / "discovery" / "androidpublisher.v3.20261015.json"


@pytest.fixture(scope="session")
def discovery():
    return json.loads(DISCOVERY.read_text(encoding="utf-8"))


def method(doc, dotted):
    """Look up a discovery method by its dotted id, e.g. 'edits.bundles.upload'."""
    *resources, name = dotted.split(".")
    node = doc
    for res in resources:
        node = node["resources"][res]
    return node["methods"][name]


@pytest.fixture(scope="session")
def rsa_pem():
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                            serialization.NoEncryption()).decode()
    return key, pem


@pytest.fixture
def sa_file(tmp_path, rsa_pem):
    path = tmp_path / "keys" / "play-kit.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"type": "service_account", "client_email": "play-kit@demo.iam.gserviceaccount.com",
                                "private_key": rsa_pem[1], "private_key_id": "kid123",
                                "token_uri": "https://oauth2.googleapis.com/token"}))
    return path


@pytest.fixture
def fake():
    from fake_play import FakePlay

    f = FakePlay()
    f.add_app("com.example.app")
    return f


@pytest.fixture
def client(fake, sa_file):
    import play_api

    sa = play_api.load_service_account(sa_file)
    return play_api.Client(play_api.TokenSource(sa, fake, now=fake.now), transport=fake, sleep=lambda s: None)
