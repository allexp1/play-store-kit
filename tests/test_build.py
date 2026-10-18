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


# ---------------------------------------------------------------- 1.0.1 fixes
def test_absolute_output_glob_is_matched_and_normalised(tmp_path):
    out = tmp_path / "cache" / "app" / "outputs" / "bundle" / "release"
    out.mkdir(parents=True)
    aab = out / "app-release.aab"
    aab.write_bytes(b"1")
    kit, root = kit_with(tmp_path / "p", {"kind": "gradle", "output_glob": str(out) + "/*.aab"})
    plan = build.adapter_plan(kit)
    assert build.select_output(plan["root"], plan["output_glob"]) == aab.resolve()
    # a relative glob that climbs out of root comes back without `..`
    rel = os.path.relpath(out, root) + "/*.aab"
    got = build.select_output(root, rel)
    assert got == aab.resolve() and ".." not in str(got)
    with pytest.raises(build.BuildError):
        build.select_output(root, str(tmp_path / "nowhere" / "*.aab"))


def _gradle_kit(tmp_path, build_gradle):
    kit, root = kit_with(tmp_path, {"kind": "gradle", "build_command": "true", "java_home": None})
    (root / "app").mkdir()
    (root / "app" / "build.gradle.kts").write_text(build_gradle)
    return kit, root


def test_moved_build_dir_is_named_in_the_error(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "pick_jdk", lambda proj: {"java_home": None, "version": 17, "source": "test"})
    kit, root = _gradle_kit(tmp_path, 'layout.buildDirectory = file("${System.getProperty("user.home")}/.cache/x")\n')
    with pytest.raises(build.BuildError) as e:
        build.bundle(kit)
    msg = str(e.value)
    assert "build succeeded but no AAB" in msg and "layout.buildDirectory" in msg and "output_glob" in msg


def test_moved_build_dir_found_through_gradle(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "pick_jdk", lambda proj: {"java_home": None, "version": 17, "source": "test"})
    moved = tmp_path / "cache" / "app"
    (moved / "outputs/bundle/release").mkdir(parents=True)
    (moved / "outputs/bundle/release/app-release.aab").write_bytes(b"1")
    kit, root = _gradle_kit(tmp_path / "p", 'layout.buildDirectory.set(file("/elsewhere"))\n')
    monkeypatch.setattr(build, "gradle_build_dir", lambda root, module, env=None: moved)
    out = build.bundle(kit)
    assert out["artifact"] == str((moved / "outputs/bundle/release/app-release.aab").resolve())
    assert "set output_glob" in out["note"].lower()


def test_no_moved_build_dir_plain_message(tmp_path, monkeypatch):
    monkeypatch.setattr(build, "pick_jdk", lambda proj: {"java_home": None, "version": 17, "source": "test"})
    kit, _ = _gradle_kit(tmp_path, "android { }\n")
    with pytest.raises(build.BuildError) as e:
        build.bundle(kit)
    assert "build succeeded but no AAB" in str(e.value) and "moves the build directory" not in str(e.value)


def test_pick_jdk_skips_old_java(tmp_path):
    versions = {"/jre8": 8, "/jdk21": 21, build.STUDIO_JBR: 21, None: 8}
    probe = versions.get
    got = build.pick_jdk({}, env={"JAVA_HOME": "/jre8"}, probe=probe, macos=lambda: None)
    assert got == {"java_home": build.STUDIO_JBR, "version": 21, "source": "Android Studio JBR"}
    got = build.pick_jdk({}, env={}, probe=probe, macos=lambda: "/jdk21")
    assert got["source"] == "/usr/libexec/java_home" and got["java_home"] == "/jdk21"
    assert build.pick_jdk({"java_home": "/jdk21"}, env={}, probe=probe)["source"] == "project.yaml java_home"
    with pytest.raises(build.BuildError):
        build.pick_jdk({"java_home": "/jre8"}, env={}, probe=probe)
    with pytest.raises(build.BuildError) as e:
        build.pick_jdk({}, env={}, probe=lambda h: 8, macos=lambda: None)
    assert "java_home" in str(e.value)


def test_inspect_cli_reads_symbol_paths_from_project_yaml(tmp_path, rsa_pem, monkeypatch, capsys):
    kit, root = kit_with(tmp_path, {"kind": "gradle", "mapping_path": "app/build/mapping.txt",
                                    "native_symbols_path": "/abs/symbols.zip"})
    (root / "app/build").mkdir(parents=True)
    (root / "app/build/mapping.txt").write_text("# map")
    aab = tmp_path / "app.aab"
    make_aab(aab, rsa_pem[0])
    monkeypatch.setattr(build, "Bundletool", lambda: StubBundletool(manifest()))
    assert build.main(["inspect", "--aab", str(aab), "--kit", str(kit)]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["mapping_path"] == str((root / "app/build/mapping.txt").resolve())
    assert out["native_symbols_path"] == "/abs/symbols.zip"
    assert any("does not exist yet" in n for n in out["notes"])
