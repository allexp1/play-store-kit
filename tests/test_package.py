"""Package hygiene: frontmatter, versions, living hash, and no secrets in what ships."""
import hashlib
import json
import re
import subprocess

from conftest import ROOT


def _front():
    text = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md has no frontmatter"
    return dict(re.findall(r"^(\w+): (.*)$", m.group(1), re.M))


def test_frontmatter():
    f = _front()
    assert f["name"] == "play-store-kit"
    desc = json.loads(f["description"])
    assert 0 < len(desc) <= 1024


def test_versions_agree():
    version = json.loads(_front()["version"])
    pkg = json.loads((ROOT / "package.json").read_text())
    assert pkg["version"] == version
    assert f"## {version} " in (ROOT / "CHANGELOG.md").read_text()


def test_living_hash_matches_skill():
    digest = hashlib.sha256((ROOT / "SKILL.md").read_bytes()).hexdigest()
    assert (ROOT / ".living" / "ORIGINAL.sha256").read_text().strip() == digest


def test_skill_size_budget():
    assert (ROOT / "SKILL.md").stat().st_size <= 12288


def test_gitignore_covers_secrets():
    ignore = (ROOT / ".gitignore").read_text()
    for pattern in ("private/", "*.jks", "*.keystore", "keystore.properties", "*service-account*.json", "*-sa.json", "assets/", "*.p8"):
        assert pattern in ignore


def test_npm_pack_has_no_secrets_or_plans():
    out = subprocess.run(["npm", "pack", "--dry-run", "--json"], cwd=ROOT, capture_output=True, text=True, check=True)
    files = [f["path"] for f in json.loads(out.stdout)[0]["files"]]
    assert "SKILL.md" in files and "scripts/rules.json" in files
    bad = [f for f in files if f.startswith(("plan/", "tests/", "private/", ".venv/"))
           or f.endswith((".jks", ".keystore", ".p8")) or "service-account" in f or "__pycache__" in f]
    assert not bad, bad
