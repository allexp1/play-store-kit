"""Synthetic App Bundles for tests: real ELF headers, real PKCS7 signer blocks, DEX bytes with SDK descriptors,
and a stub bundletool that answers with the manifest XML each fixture declares (PLAN-FINAL §15.6)."""
from __future__ import annotations

import json
import struct
import zipfile

A = "http://schemas.android.com/apk/res/android"


def elf(aligns, is64=True, machine=183):
    """A minimal ELF shared object whose PT_LOAD headers carry the given p_align values."""
    n = len(aligns) + 1  # plus one PT_DYNAMIC that must be ignored
    if is64:
        hdr = b"\x7fELF" + bytes([2, 1, 1]) + b"\0" * 9
        hdr += struct.pack("<HHIQQQIHHHHHH", 3, machine, 1, 0, 64, 0, 0, 64, 56, n, 64, 0, 0)
        ph = b"".join(struct.pack("<IIQQQQQQ", 1, 5, 0, 0, 0, 0x1000, 0x1000, a) for a in aligns)
        ph += struct.pack("<IIQQQQQQ", 2, 6, 0, 0, 0, 0, 0, 8)
    else:
        hdr = b"\x7fELF" + bytes([1, 1, 1]) + b"\0" * 9
        hdr += struct.pack("<HHIIIIIHHHHHH", 3, 40, 1, 0, 52, 0, 0, 52, 32, n, 40, 0, 0)
        ph = b"".join(struct.pack("<IIIIIIII", 1, 0, 0, 0, 0x1000, 0x1000, 5, a) for a in aligns)
        ph += struct.pack("<IIIIIIII", 2, 0, 0, 0, 0, 0, 6, 4)
    return hdr + ph


def pkcs7(key, subject="CN=Upload Key,O=Example"):
    import datetime as dt

    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.serialization import pkcs7 as p7
    from cryptography.x509.oid import NameOID

    attrs = []
    for part in subject.split(","):
        k, v = part.split("=", 1)
        attrs.append(x509.NameAttribute({"CN": NameOID.COMMON_NAME, "O": NameOID.ORGANIZATION_NAME,
                                         "C": NameOID.COUNTRY_NAME}[k.strip()], v))
    name = x509.Name(attrs)
    now = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=9000))
            .sign(key, hashes.SHA256()))
    blob = (p7.PKCS7SignatureBuilder().set_data(b"MANIFEST").add_signer(cert, key, hashes.SHA256())
            .sign(serialization.Encoding.DER, [p7.PKCS7Options.DetachedSignature]))
    import hashlib
    return blob, hashlib.sha256(cert.public_bytes(serialization.Encoding.DER)).hexdigest()


def manifest(package="com.example.app", version_code=42, version_name="1.4.0", min_sdk=24, target_sdk=36,
             permissions=(), features=(), meta=None, locale_config=False, debuggable=False):
    perms = "".join(f'<uses-permission android:name="{p}"/>' for p in permissions)
    feats = "".join(f'<uses-feature android:name="{n}" android:required="{str(r).lower()}"/>' for n, r in features)
    md = "".join(f'<meta-data android:name="{k}" android:value="{v}"/>' for k, v in (meta or {}).items())
    app_attrs = (' android:localeConfig="@xml/locales_config"' if locale_config else "") + \
                (' android:debuggable="true"' if debuggable else "")
    return (f'<manifest xmlns:android="{A}" package="{package}" android:versionCode="{version_code}" '
            f'android:versionName="{version_name}"><uses-sdk android:minSdkVersion="{min_sdk}" '
            f'android:targetSdkVersion="{target_sdk}"/>{perms}{feats}<application{app_attrs}>{md}</application>'
            f'</manifest>')


def make_aab(path, key=None, libs=None, dex=b"dex\n035\0", signer_subject="CN=Upload Key,O=Example",
             compress_libs=False, signed=True):
    """libs: {"arm64-v8a/libfoo.so": [16384, 16384], "armeabi-v7a/libfoo.so": ([4096], False)}"""
    sha = None
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("BundleConfig.pb", b"\x0a\x00")
        zf.writestr("base/manifest/AndroidManifest.xml", b"proto")
        zf.writestr("base/dex/classes.dex", dex)
        for rel, spec in (libs or {}).items():
            aligns, is64 = (spec if isinstance(spec, tuple) else (spec, "64" in rel))
            zf.writestr(zipfile.ZipInfo(f"base/lib/{rel}"), elf(aligns, is64),
                        compress_type=zipfile.ZIP_DEFLATED if compress_libs else zipfile.ZIP_STORED)
        if signed and key is not None:
            blob, sha = pkcs7(key, signer_subject)
            zf.writestr("META-INF/UPLOAD.RSA", blob)
            zf.writestr("META-INF/MANIFEST.MF", b"Manifest-Version: 1.0\n")
    return sha


class StubBundletool:
    """Answers validate / dump manifest / dump config / build-apks / get-size like bundletool would."""

    def __init__(self, manifest_xml, config=None, size_csv=None, fail=()):
        self.manifest_xml, self.config, self.size_csv, self.fail = manifest_xml, config or {}, size_csv, set(fail)
        self.calls = []

    def run(self, args, timeout=600):
        import build
        self.calls.append(args)
        key = " ".join(args[:2]) if args[0] in ("dump", "get-size") else args[0]
        if key in self.fail:
            raise build.BuildError(f"bundletool {key} failed (stub)")
        if args[0] == "validate":
            return "ok"
        if key == "dump manifest":
            return self.manifest_xml
        if key == "dump config":
            return json.dumps(self.config)
        if args[0] == "build-apks":
            return ""
        if key == "get-size total":
            return self.size_csv or "MIN,MAX\n1000,2000\n"
        raise build.BuildError(f"stub: unexpected {args}")
