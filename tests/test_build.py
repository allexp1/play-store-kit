"""build.py: ELF alignment, signer, manifest, DEX markers, adapters, bump (§15.6)."""
import json
import os
import time

import pytest

import build
from artifacts import StubBundletool, elf, make_aab, manifest


def test_elf_64_and_32():
    assert build.elf_load_alignments(elf([16384, 65536])) == [16384, 65536]
    assert build.elf_load_alignments(elf([4096], is64=False)) == [4096]
    with pytest.raises(build.BuildError):
        build.elf_load_alignments(b"not elf at all, really not" * 3)
    with pytest.raises(build.BuildError):
        build.elf_load_alignments(elf([4096])[:80])


def test_inspect_reads_everything(tmp_path, rsa_pem):
    aab = tmp_path / "app.aab"
    sha = make_aab(aab, rsa_pem[0], libs={"arm64-v8a/libgood.so": [16384, 16384],
                                         "arm64-v8a/libbad.so": [16384, 4096],
                                         "armeabi-v7a/libold.so": [4096]},
                   dex=b"dex\n035\0Lcom/stripe/android/Stripe;Lcom/google/android/gms/ads/AdView;")
    tool = StubBundletool(manifest(permissions=["android.permission.CAMERA"],
                                   meta={"com.google.android.play.billingclient.version": "8.0.0"}),
                          config={"optimizations": {"uncompressNativeLibraries": {"enabled": True,
                                                                                 "alignment": "PAGE_ALIGNMENT_16K"}}})
    rec = build.inspect(aab, tool, size=True)
    assert (rec.package, rec.version_code, rec.version_name, rec.target_sdk) == ("com.example.app", 42, "1.4.0", 36)
    assert rec.signer_sha256 == sha and "Upload Key" in rec.signer_subject
    assert rec.abis == ["arm64-v8a", "armeabi-v7a"]
    bad = {l.path for l in rec.native_libs if l.bad_segments}
    assert bad == {"base/lib/arm64-v8a/libbad.so", "base/lib/armeabi-v7a/libold.so"}
    assert "Lcom/stripe/" in rec.dex_strings and "Lcom/google/android/gms/ads/" in rec.dex_strings
    assert rec.page_alignment_16k_config is True and rec.compressed_download_bytes == {"min": 1000, "max": 2000}
    assert rec.meta_data["com.google.android.play.billingclient.version"] == "8.0.0"
    assert json.dumps(rec.to_json(), default=list)


def test_manifest_with_entities_refused():
    rec = build.ArtifactRecord("x", "0", 0)
    with pytest.raises(build.BuildError):
        build.parse_manifest('<!DOCTYPE m [<!ENTITY a "x">]><manifest/>', rec)


def test_compressed_native_flagged(tmp_path, rsa_pem):
    aab = tmp_path / "app.aab"
    make_aab(aab, rsa_pem[0], libs={"arm64-v8a/libz.so": [16384]}, compress_libs=True)
    rec = build.inspect(aab, StubBundletool(manifest()))
    assert rec.native_libs[0].compressed and not rec.native_libs[0].bad_segments


def test_unsigned_bundle(tmp_path):
    aab = tmp_path / "app.aab"
    make_aab(aab, None, signed=False)
    assert build.inspect(aab, StubBundletool(manifest())).signer_sha256 is None


def test_size_unknown_when_build_apks_fails(tmp_path, rsa_pem):
    aab = tmp_path / "app.aab"
    make_aab(aab, rsa_pem[0])
    rec = build.inspect(aab, StubBundletool(manifest(), fail={"build-apks"}), size=True)
    assert rec.compressed_download_bytes is None


# ---------------------------------------------------------------- adapters
def kit_with(tmp_path, project):
    kit = tmp_path / "proj" / "play-store-kit"
    kit.mkdir(parents=True)
    import yaml
    (kit / "project.yaml").write_text(yaml.safe_dump(project))
    return kit, tmp_path / "proj"


def test_gradle_plan_and_newest_output(tmp_path):
    kit, root = kit_with(tmp_path, {"kind": "gradle", "module": "app", "variant": "prodRelease"})
    plan = build.adapter_plan(kit)
    assert plan["command"] == "./gradlew :app:bundleProdRelease"
    out = root / "app/build/outputs/bundle/prodRelease"
    out.mkdir(parents=True)
    old, new = out / "app-old.aab", out / "app-prod-release.aab"
    old.write_bytes(b"1")
    new.write_bytes(b"2")
    os.utime(old, (time.time() - 100, time.time() - 100))
    other = root / "wear/build/outputs/bundle/prodRelease"
    other.mkdir(parents=True)
    (other / "wear.aab").write_bytes(b"3")      # another module must never be picked
    assert build.select_output(plan["root"], plan["output_glob"]) == new


def test_react_native_runs_in_android_dir(tmp_path):
    kit, root = kit_with(tmp_path, {"kind": "react-native"})
    plan = build.adapter_plan(kit)
    assert plan["root"].endswith("/android") and plan["command"] == "./gradlew :app:bundleRelease"


def test_unity_without_command_gives_instructions(tmp_path):
    kit, _ = kit_with(tmp_path, {"kind": "unity"})
    assert "IL2CPP" in build.adapter_plan(kit)["instructions"]


def test_unknown_kind(tmp_path):
    kit, _ = kit_with(tmp_path, {"kind": "cordova"})
    with pytest.raises(build.BuildError):
        build.adapter_plan(kit)


def test_bump_gradle_kts_literal(tmp_path):
    kit, root = kit_with(tmp_path, {"kind": "gradle"})
    (root / "app").mkdir()
    f = root / "app/build.gradle.kts"
    f.write_text('android {\n  defaultConfig {\n    versionCode = 41\n    versionName = "1.3.0"\n  }\n}\n')
    r = build.bump(kit, version_name="1.4.0")
    assert r["version_code"] == 42 and "versionCode = 42" in f.read_text() and '"1.4.0"' in f.read_text()


def test_bump_refuses_expressions(tmp_path):
    kit, root = kit_with(tmp_path, {"kind": "gradle"})
    (root / "app").mkdir()
    f = root / "app/build.gradle"
    f.write_text("android { defaultConfig {\n versionCode computeCode()\n } }\n")
    before = f.read_text()
    r = build.bump(kit)
    assert not r["changed"] and "computed" in r["instructions"] and f.read_text() == before


def test_bump_flutter_and_expo(tmp_path):
    kit, root = kit_with(tmp_path, {"kind": "flutter"})
    (root / "pubspec.yaml").write_text("name: x\nversion: 1.2.3+7\n")
    assert build.bump(kit)["version_code"] == 8 and "1.2.3+8" in (root / "pubspec.yaml").read_text()
    kit2, root2 = kit_with(tmp_path / "e", {"kind": "expo"})
    (root2 / "app.json").write_text(json.dumps({"expo": {"version": "1.0.0", "android": {"versionCode": 3}}}))
    assert build.bump(kit2, write=False)["version_code"] == 4
    assert json.loads((root2 / "app.json").read_text())["expo"]["android"]["versionCode"] == 3   # dry run


def test_bundle_dry_run_runs_nothing(tmp_path):
    kit, _ = kit_with(tmp_path, {"kind": "gradle"})
    assert build.bundle(kit, dry_run=True)["dry_run"] is True
