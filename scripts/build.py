#!/usr/bin/env python3
"""Build adapters and artifact inspection (PLAN-FINAL §9).

  python3 scripts/build.py inspect   --aab PATH [--kit KIT] [--size] [--json]
  python3 scripts/build.py bundle    --kit KIT [--dry-run]            # runs the adapter's build (no shell), finds the AAB
  python3 scripts/build.py bump      --kit KIT [--version-name X] [--dry-run]
  python3 scripts/build.py elf-check --aab PATH [--json]
  python3 scripts/build.py size      --aab PATH

Every adapter (gradle, flutter, react-native, expo, unity, external) ends in the same ArtifactRecord, read from the
built AAB itself: the merged manifest through `bundletool dump manifest` (never hand-merged from src/), the bundle
config, the zip listing (native libraries per ABI, DEX bytes), the signer certificate from META-INF (PKCS7, via
`cryptography`) and, with --size, the compressed download size from `bundletool get-size total`.

The ELF check is pure Python: it reads the program headers of every packaged .so and reports each PT_LOAD
segment whose p_align is below 16384 on 64-bit ABIs. PAGE_ALIGNMENT_16K in the bundle config is only packaging
intent and is reported, not trusted.

`bump` edits only an integer literal (Gradle `versionCode = N` / `versionCode N`, Flutter `version: x.y.z+N`,
Expo `android.versionCode`); anything else (an expression, a property, a plugin) gets instructions instead.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import pathlib
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile

ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
ABIS_64 = ("arm64-v8a", "x86_64")
PT_LOAD = 1
PAGE_16K = 16384
ADAPTERS = ("gradle", "flutter", "react-native", "expo", "unity", "external")


class BuildError(Exception):
    pass


# ------------------------------------------------------------------------------------------------ record
@dataclasses.dataclass
class NativeLib:
    path: str
    abi: str
    module: str
    min_load_align: int | None
    bad_segments: int
    compressed: bool
    note: str = ""


@dataclasses.dataclass
class ArtifactRecord:
    path: str
    sha256: str
    size_bytes: int
    package: str | None = None
    version_code: int | None = None
    version_name: str | None = None
    min_sdk: int | None = None
    target_sdk: int | None = None
    signer_sha256: str | None = None
    signer_subject: str | None = None
    abis: list[str] = dataclasses.field(default_factory=list)
    native_libs: list[NativeLib] = dataclasses.field(default_factory=list)
    modules: list[str] = dataclasses.field(default_factory=list)
    dex_total_bytes: int = 0
    dex_strings: set[str] = dataclasses.field(default_factory=set, repr=False)
    permissions: list[str] = dataclasses.field(default_factory=list)
    features: dict[str, bool] = dataclasses.field(default_factory=dict)   # name -> required
    meta_data: dict[str, str] = dataclasses.field(default_factory=dict)
    locale_config: bool = False
    extract_native_libs: str | None = None
    debuggable: bool = False
    page_alignment_16k_config: bool | None = None
    compressed_download_bytes: dict[str, int] | None = None   # {"min":…, "max":…} from get-size total
    build_id: str | None = None
    mapping_path: str | None = None
    native_symbols_path: str | None = None
    manifest_source: str = "bundletool"
    notes: list[str] = dataclasses.field(default_factory=list)

    def to_json(self):
        d = dataclasses.asdict(self)
        d.pop("dex_strings")
        return d


def sha256_file(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


# ------------------------------------------------------------------------------------------------ ELF
def elf_load_alignments(data: bytes) -> list[int]:
    """p_align of every PT_LOAD program header of an ELF file (32- or 64-bit, either endianness)."""
    if len(data) < 52 or data[:4] != b"\x7fELF":
        raise BuildError("not an ELF file")
    is64 = data[4] == 2
    endian = "<" if data[5] == 1 else ">"
    if is64:
        if len(data) < 64:
            raise BuildError("truncated ELF header")
        e_phoff, = struct.unpack_from(endian + "Q", data, 32)
        e_phentsize, e_phnum = struct.unpack_from(endian + "HH", data, 54)
    else:
        e_phoff, = struct.unpack_from(endian + "I", data, 28)
        e_phentsize, e_phnum = struct.unpack_from(endian + "HH", data, 42)
    aligns = []
    for i in range(e_phnum):
        off = e_phoff + i * e_phentsize
        if off + e_phentsize > len(data):
            raise BuildError("program header table runs past the end of the file")
        p_type, = struct.unpack_from(endian + "I", data, off)
        if p_type != PT_LOAD:
            continue
        if is64:
            p_align, = struct.unpack_from(endian + "Q", data, off + 48)
        else:
            p_align, = struct.unpack_from(endian + "I", data, off + 28)
        aligns.append(p_align)
    return aligns


def _native_libs(zf: zipfile.ZipFile) -> list[NativeLib]:
    libs = []
    for info in zf.infolist():
        m = re.match(r"^([^/]+)/lib/([^/]+)/([^/]+\.so)$", info.filename)
        if not m:
            continue
        module, abi, _ = m.groups()
        try:
            aligns = elf_load_alignments(zf.read(info))
        except BuildError as e:
            libs.append(NativeLib(info.filename, abi, module, None, 0, info.compress_type != zipfile.ZIP_STORED,
                                  f"unreadable: {e}"))
            continue
        bad = sum(1 for a in aligns if a < PAGE_16K)
        libs.append(NativeLib(info.filename, abi, module, min(aligns) if aligns else None, bad,
                              info.compress_type != zipfile.ZIP_STORED))
    return libs


# ------------------------------------------------------------------------------------------------ signer
def signer_certificate(zf: zipfile.ZipFile):
    """(sha256 hex of the DER certificate, subject string) from META-INF/*.RSA|*.EC|*.DSA, or (None, None)."""
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7

    for name in zf.namelist():
        if re.match(r"^META-INF/[^/]+\.(RSA|EC|DSA)$", name, re.I):
            certs = pkcs7.load_der_pkcs7_certificates(zf.read(name))
            if certs:
                cert: x509.Certificate = certs[0]
                der = cert.public_bytes(serialization.Encoding.DER)
                return hashlib.sha256(der).hexdigest(), cert.subject.rfc4514_string()
    return None, None


# ------------------------------------------------------------------------------------------------ DEX
SDK_PREFIXES = (
    "Lcom/stripe/", "Lcom/paypal/", "Lcom/braintreepayments/", "Lcom/android/billingclient/",
    "Lcom/google/android/gms/ads/", "Lcom/facebook/ads/", "Lcom/applovin/", "Lcom/unity3d/ads/",
    "Lcom/google/firebase/analytics/", "Lcom/appsflyer/", "Lcom/adjust/sdk/", "Lcom/facebook/appevents/",
    "Lcom/google/firebase/crashlytics/", "Lio/sentry/", "Lcom/amplitude/", "Lcom/mixpanel/",
    "Lcom/google/firebase/auth/", "Lcom/auth0/", "Lio/github/jan/supabase/", "Lcom/amazonaws/mobileconnectors/cognito",
    "Lcom/google/android/gms/auth/", "Landroidx/credentials/", "Lcom/google/android/libraries/identity/googleid/",
    "Lcom/google/ai/client/generativeai/", "Lcom/google/firebase/ai/", "Lcom/google/firebase/vertexai/",
    "Lcom/openai/", "Lcom/anthropic/", "Lcom/google/mlkit/genai/", "Lcom/google/android/gms/location/",
)


def dex_markers(blobs) -> set[str]:
    """Which known SDK package prefixes appear as class descriptors in the DEX string tables."""
    found = set()
    for data in blobs:
        for prefix in SDK_PREFIXES:
            if prefix not in found and prefix.encode() in data:
                found.add(prefix)
    return found


# ------------------------------------------------------------------------------------------------ bundletool
class Bundletool:
    """Runs bundletool (on PATH, or `java -jar $BUNDLETOOL_JAR`). Tests replace `run`."""

    def __init__(self, cmd=None):
        self.cmd = cmd or self._find()

    @staticmethod
    def _find():
        import os
        if shutil.which("bundletool"):
            return ["bundletool"]
        jar = os.environ.get("BUNDLETOOL_JAR")
        if jar and shutil.which("java"):
            return ["java", "-jar", jar]
        return None

    def run(self, args, timeout=600) -> str:
        if not self.cmd:
            raise BuildError("bundletool not found: install it (brew install bundletool) or set BUNDLETOOL_JAR")
        p = subprocess.run(self.cmd + args, capture_output=True, text=True, timeout=timeout)
        if p.returncode != 0:
            raise BuildError(f"bundletool {args[0]} failed: {p.stderr.strip()[:400]}")
        return p.stdout


def parse_manifest(xml_text: str, rec: ArtifactRecord):
    if re.search(r"<!(DOCTYPE|ENTITY)", xml_text, re.I):
        raise BuildError("the dumped manifest declares a DOCTYPE or ENTITY; refusing to parse it")
    root = ET.fromstring(xml_text)
    rec.package = root.get("package")
    vc = root.get(ANDROID_NS + "versionCode")
    rec.version_code = int(vc) if vc and vc.isdigit() else None
    rec.version_name = root.get(ANDROID_NS + "versionName")
    sdk = root.find("uses-sdk")
    if sdk is not None:
        for attr, field in (("minSdkVersion", "min_sdk"), ("targetSdkVersion", "target_sdk")):
            v = sdk.get(ANDROID_NS + attr)
            if v and v.isdigit():
                setattr(rec, field, int(v))
    rec.permissions = sorted({p.get(ANDROID_NS + "name") for p in root.iter()
                              if p.tag in ("uses-permission", "uses-permission-sdk-23") and p.get(ANDROID_NS + "name")})
    for f in root.findall("uses-feature"):
        name = f.get(ANDROID_NS + "name")
        if name:
            rec.features[name] = f.get(ANDROID_NS + "required", "true") != "false"
    app = root.find("application")
    if app is not None:
        rec.locale_config = app.get(ANDROID_NS + "localeConfig") is not None
        rec.extract_native_libs = app.get(ANDROID_NS + "extractNativeLibs")
        rec.debuggable = app.get(ANDROID_NS + "debuggable") == "true"
        for md in app.findall("meta-data"):
            name = md.get(ANDROID_NS + "name")
            if name:
                rec.meta_data[name] = md.get(ANDROID_NS + "value") or md.get(ANDROID_NS + "resource") or ""


def inspect(aab, tool: Bundletool | None = None, size=False, mapping=None, native_symbols=None) -> ArtifactRecord:
    aab = pathlib.Path(aab)
    if not aab.exists():
        raise BuildError(f"{aab} does not exist")
    tool = tool or Bundletool()
    rec = ArtifactRecord(str(aab), sha256_file(aab), aab.stat().st_size,
                         mapping_path=str(mapping) if mapping else None,
                         native_symbols_path=str(native_symbols) if native_symbols else None)
    tool.run(["validate", f"--bundle={aab}"])
    parse_manifest(tool.run(["dump", "manifest", f"--bundle={aab}"]), rec)
    try:
        config = json.loads(tool.run(["dump", "config", f"--bundle={aab}"]) or "{}")
        unc = config.get("optimizations", {}).get("uncompressNativeLibraries", {})
        rec.page_alignment_16k_config = unc.get("alignment") == "PAGE_ALIGNMENT_16K" if unc else None
    except (BuildError, ValueError) as e:
        rec.notes.append(f"bundle config not read: {e}")
    with zipfile.ZipFile(aab) as zf:
        rec.modules = sorted({n.split("/", 1)[0] for n in zf.namelist()
                              if "/" in n and not n.startswith(("META-INF/", "BUNDLE-METADATA/"))
                              and n.split("/", 1)[0] not in ("BundleConfig.pb",)})
        rec.native_libs = _native_libs(zf)
        rec.abis = sorted({lib.abi for lib in rec.native_libs})
        dex = [zf.read(n) for n in zf.namelist() if re.match(r"^[^/]+/dex/[^/]+\.dex$", n)]
        rec.dex_total_bytes = sum(len(d) for d in dex)
        rec.dex_strings = dex_markers(dex)
        rec.signer_sha256, rec.signer_subject = signer_certificate(zf)
    if size:
        rec.compressed_download_bytes = download_size(aab, tool)
    return rec


def download_size(aab, tool: Bundletool) -> dict | None:
    """Compressed download size from `bundletool get-size total` (needs a debug keystore for build-apks)."""
    with tempfile.TemporaryDirectory() as tmp:
        apks = pathlib.Path(tmp) / "out.apks"
        try:
            tool.run(["build-apks", f"--bundle={aab}", f"--output={apks}"])
            out = tool.run(["get-size", "total", f"--apks={apks}"])
        except BuildError:
            return None
    lines = [l for l in out.strip().splitlines() if l.strip()]
    if len(lines) < 2:
        return None
    head = [h.strip().upper() for h in lines[0].split(",")]
    vals = [v.strip() for v in lines[1].split(",")]
    row = dict(zip(head, vals))
    try:
        return {"min": int(row["MIN"]), "max": int(row["MAX"])}
    except (KeyError, ValueError):
        return None


# ------------------------------------------------------------------------------------------------ adapters
def load_yaml(path):
    import yaml
    p = pathlib.Path(path)
    return (yaml.safe_load(p.read_text(encoding="utf-8")) or {}) if p.exists() else {}


def _cap(variant):
    return variant[:1].upper() + variant[1:]


def adapter_plan(kit) -> dict:
    """What `bundle` would run and where it looks for the output, from KIT/project.yaml."""
    kit = pathlib.Path(kit)
    proj = load_yaml(kit / "project.yaml")
    kind = proj.get("kind")
    if kind not in ADAPTERS:
        raise BuildError(f"project.yaml kind must be one of {', '.join(ADAPTERS)}")
    root = (kit / proj.get("root", "..")).resolve()
    module = proj.get("module", "app")
    variant = proj.get("variant", "release")
    if kind == "external":
        return {"kind": kind, "root": str(root), "command": None, "output_glob": proj.get("output_glob")}
    if kind == "flutter":
        cmd = proj.get("build_command") or "flutter build appbundle --release"
        glob = proj.get("output_glob") or "build/app/outputs/bundle/release/*.aab"
    elif kind == "unity":
        cmd = proj.get("build_command")
        glob = proj.get("output_glob") or f"launcher/build/outputs/bundle/{variant}/*.aab"
        if not cmd:
            return {"kind": kind, "root": str(root), "command": None, "output_glob": glob,
                    "instructions": "Build the AAB from Unity (IL2CPP, ARM64) or set build_command for the "
                                    "exported Gradle project, then run inspect"}
    else:
        android = "android/" if kind in ("react-native", "expo") else ""
        cmd = proj.get("build_command") or f"./gradlew :{module}:bundle{_cap(variant)}"
        if android and not proj.get("build_command"):
            root = root / "android"
            android = ""
        glob = proj.get("output_glob") or f"{android}{module}/build/outputs/bundle/{variant}/*.aab"
    return {"kind": kind, "root": str(root), "command": cmd, "output_glob": glob}


def select_output(root, glob) -> pathlib.Path:
    """Newest .aab matching the glob below the selected module. Ties and stale files are reported by the caller."""
    found = sorted(pathlib.Path(root).glob(glob), key=lambda p: p.stat().st_mtime, reverse=True)
    if not found:
        raise BuildError(f"no AAB matches {glob} under {root}")
    return found[0]


def bump(kit, version_name=None, write=True) -> dict:
    """Raise the version code by one where it is a plain integer literal; otherwise return instructions."""
    kit = pathlib.Path(kit)
    plan = adapter_plan(kit)
    proj = load_yaml(kit / "project.yaml")
    root = (kit / proj.get("root", "..")).resolve()   # the project root, not the adapter's build directory
    kind = plan["kind"]
    if kind == "flutter":
        f = root / "pubspec.yaml"
        text = f.read_text(encoding="utf-8")
        m = re.search(r"^version:\s*([0-9A-Za-z.\-]+)\+(\d+)\s*$", text, re.M)
        if not m:
            return {"changed": False, "instructions": "pubspec.yaml has no `version: x.y.z+N`; set it by hand"}
        name, code = version_name or m.group(1), int(m.group(2)) + 1
        new = text[:m.start()] + f"version: {name}+{code}" + text[m.end():]
        return _write(f, text, new, code, write)
    if kind == "expo":
        f = root / "app.json"
        doc = json.loads(f.read_text(encoding="utf-8"))
        android = doc.get("expo", doc).get("android", {})
        if not isinstance(android.get("versionCode"), int):
            return {"changed": False, "instructions": "app.json has no integer android.versionCode; set it by hand"}
        android["versionCode"] += 1
        if version_name:
            doc.get("expo", doc)["version"] = version_name
        return _write(f, f.read_text(encoding="utf-8"), json.dumps(doc, indent=2) + "\n", android["versionCode"],
                      write)
    if kind in ("gradle", "react-native"):
        module = proj.get("module", "app")
        base = root / ("android" if kind == "react-native" else "") / module
        for name in ("build.gradle.kts", "build.gradle"):
            f = base / name
            if f.exists():
                text = f.read_text(encoding="utf-8")
                hits = list(re.finditer(r"^(\s*versionCode\s*(?:=\s*)?)(\d+)(\s*)$", text, re.M))
                if len(hits) != 1:
                    return {"changed": False, "instructions":
                            f"{f.name}: versionCode is not a single integer literal (found {len(hits)}); "
                            "it is computed or plugin-managed, so raise it where it is defined"}
                h = hits[0]
                code = int(h.group(2)) + 1
                new = text[:h.start()] + f"{h.group(1)}{code}{h.group(3)}" + text[h.end():]
                if version_name:
                    new = re.sub(r'^(\s*versionName\s*(?:=\s*)?)"[^"]*"', lambda m: f'{m.group(1)}"{version_name}"',
                                 new, count=1, flags=re.M)
                return _write(f, text, new, code, write)
        return {"changed": False, "instructions": f"no build.gradle(.kts) in {base}"}
    return {"changed": False, "instructions": f"{kind}: raise the version code where the project defines it "
                                              "(Unity: Player Settings → Bundle Version Code)"}


def _write(path, old, new, code, write):
    if write and new != old:
        path.write_text(new, encoding="utf-8")
    return {"changed": new != old, "file": str(path), "version_code": code, "written": bool(write)}


def bundle(kit, dry_run=False) -> dict:
    """Run the adapter's build (when it has one) and pick the newest AAB it produced."""
    plan = adapter_plan(kit)
    if dry_run:
        return {**plan, "dry_run": True}
    if plan["command"]:
        # build_command is one command line from the owner's project.yaml, run without a shell (no && or pipes).
        p = subprocess.run(shlex.split(plan["command"]), cwd=plan["root"], capture_output=True, text=True)
        if p.returncode != 0:
            raise BuildError(f"build failed ({p.returncode}): {p.stderr.strip()[-800:]}")
    out = dict(plan)
    if plan.get("output_glob"):
        out["artifact"] = str(select_output(plan["root"], plan["output_glob"]))
    return out


# ------------------------------------------------------------------------------------------------ CLI
def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("cmd", choices=["inspect", "bundle", "bump", "elf-check", "size"])
    p.add_argument("--aab")
    p.add_argument("--kit", default="./play-store-kit")
    p.add_argument("--size", action="store_true")
    p.add_argument("--version-name")
    p.add_argument("--json", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    try:
        if args.cmd == "bundle":
            print(json.dumps(bundle(args.kit, args.dry_run), indent=2))
        elif args.cmd == "bump":
            print(json.dumps(bump(args.kit, args.version_name, write=not args.dry_run), indent=2))
        elif args.cmd == "elf-check":
            with zipfile.ZipFile(args.aab) as zf:
                libs = _native_libs(zf)
            bad = [l for l in libs if l.abi in ABIS_64 and l.bad_segments]
            print(json.dumps([dataclasses.asdict(l) for l in libs], indent=2) if args.json else
                  "\n".join(f"{'FAIL' if l in bad else 'ok  ' if l.abi in ABIS_64 else 'n/a '} {l.path} "
                            f"min p_align={l.min_load_align}{'' if l.abi in ABIS_64 else ' (32-bit, not checked)'}"
                            for l in libs)
                  or "no native libraries")
            return 1 if bad else 0
        elif args.cmd == "size":
            print(json.dumps(download_size(args.aab, Bundletool()), indent=2))
        else:
            rec = inspect(args.aab, size=args.size)
            print(json.dumps(rec.to_json(), indent=2, default=list))
        return 0
    except BuildError as e:
        print(e, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
