#!/usr/bin/env python3
"""Pre-check a built App Bundle against Google Play's requirements (PLAN-FINAL §10; catalogue in
references/precheck-rules.md).

  python3 scripts/precheck.py --aab PATH [--kit KIT] [--project DIR] [--size] [--today YYYY-MM-DD] [--json] [--strict]
  python3 scripts/precheck.py PROJECT [--kit KIT] ...       # finds the AAB through the kit's build adapter

Severity: BLOCKER (objective; artifact-class BLOCKERs can never be waived), LIKELY (rejection likely), WARNING,
QUESTION (needs an answer in facts.yaml; a `blocking` question stops submission because a form cannot be filled),
INFO. Each finding carries its evidence class: artifact (read from the AAB / merged manifest), remote (an API
snapshot), console (a Console observation), attested (the person's dated statement), inference (an SDK or keyword
hint), question (no answer yet). Values come from scripts/rules.json, never from memory.

Waivers live in KIT/precheck-decisions.yaml: [{rule, reason, date, decided_by}]; they apply to inference and
question findings only. Extensions granted in Policy status go in KIT/policy-extensions.yaml:
[{kind: target_sdk|billing_library, until: YYYY-MM-DD, observed_at, source: console}].

Writes KIT/PRECHECK.md. Exit 1 on a BLOCKER or a blocking QUESTION (--strict: also LIKELY), else 0.
"""
from __future__ import annotations

import argparse
import dataclasses
import datetime as dt
import json
import pathlib
import subprocess
import sys

import build
import rules as rules_mod

SEVERITIES = ("BLOCKER", "LIKELY", "WARNING", "QUESTION", "INFO")
CLASSES = ("artifact", "remote", "console", "attested", "inference", "question", "project", "facts")

PAYMENT_SDKS = ("Lcom/stripe/", "Lcom/paypal/", "Lcom/braintreepayments/")
ADS_SDKS = ("Lcom/google/android/gms/ads/", "Lcom/facebook/ads/", "Lcom/applovin/", "Lcom/unity3d/ads/",
            "Lcom/google/firebase/analytics/", "Lcom/appsflyer/", "Lcom/adjust/sdk/", "Lcom/facebook/appevents/")
COLLECTING_SDKS = ADS_SDKS + ("Lcom/google/firebase/crashlytics/", "Lio/sentry/", "Lcom/amplitude/",
                              "Lcom/mixpanel/", "Lcom/google/android/gms/location/")
ACCOUNT_SDKS = ("Lcom/google/firebase/auth/", "Lcom/auth0/", "Lio/github/jan/supabase/",
                "Lcom/amazonaws/mobileconnectors/cognito")
# Only sign-in entry points (play-services-auth). Not gms/auth/ or GoogleSignInAccount: Play Billing pulls those in
# through play-services-base, sign-in or not.
SIGNIN_SDKS = ACCOUNT_SDKS + ("Lcom/google/android/gms/auth/api/signin/GoogleSignIn;",
                              "Lcom/google/android/gms/auth/api/signin/GoogleSignInClient;",
                              "Lcom/google/android/gms/auth/api/identity/", "Landroidx/credentials/",
                              "Lcom/google/android/libraries/identity/googleid/")
GENAI_SDKS = ("Lcom/google/ai/client/generativeai/", "Lcom/google/firebase/ai/", "Lcom/google/firebase/vertexai/",
              "Lcom/openai/", "Lcom/anthropic/", "Lcom/google/mlkit/genai/")
BILLING_SDK = "Lcom/android/billingclient/"

SENSITIVE_PERMISSIONS = {
    "android.permission.READ_SMS": "SMS and Call Log permissions declaration",
    "android.permission.SEND_SMS": "SMS and Call Log permissions declaration",
    "android.permission.RECEIVE_SMS": "SMS and Call Log permissions declaration",
    "android.permission.READ_CALL_LOG": "SMS and Call Log permissions declaration",
    "android.permission.WRITE_CALL_LOG": "SMS and Call Log permissions declaration",
    "android.permission.PROCESS_OUTGOING_CALLS": "SMS and Call Log permissions declaration",
    "android.permission.MANAGE_EXTERNAL_STORAGE": "All files access declaration",
    "android.permission.ACCESS_BACKGROUND_LOCATION": "Location permissions declaration (background)",
    "android.permission.SCHEDULE_EXACT_ALARM": "Exact alarm declaration",
    "android.permission.USE_EXACT_ALARM": "Exact alarm declaration",
    "android.permission.READ_MEDIA_IMAGES": "Photo and video permissions declaration",
    "android.permission.READ_MEDIA_VIDEO": "Photo and video permissions declaration",
    "android.permission.QUERY_ALL_PACKAGES": "Package (app) visibility declaration",
    "android.permission.REQUEST_INSTALL_PACKAGES": "Request install packages declaration",
    "android.permission.BIND_ACCESSIBILITY_SERVICE": "Accessibility API declaration",
    "android.permission.BIND_VPN_SERVICE": "VpnService declaration",
    "android.permission.USE_FULL_SCREEN_INTENT": "Full-screen intent declaration",
}
FGS_PREFIX = "android.permission.FOREGROUND_SERVICE_"
DATED_PERMISSION_CHANGES = {   # 2027-01-27 changes (RESEARCH A.7)
    "android.permission.READ_CALL_LOG": "READ_CALL_LOG for verification flows changes on 2027-01-27",
    "android.permission.READ_CONTACTS": "Contacts permissions policy changes on 2027-01-27",
    "android.permission.FOREGROUND_SERVICE_LOCATION": "geofencing foreground-service use case changes on 2027-01-27",
}
REGULATED = {"financial": "R-FINANCE", "health": "R-HEALTH", "government": "R-GOV", "ugc": "R-UGC",
             "children_audience": "R-FAMILIES"}
ORG_REQUIRED = ("financial", "health", "vpn", "government")
FORM_FEATURES = {"android.hardware.type.watch": "wear", "android.hardware.type.automotive": "automotive",
                 "android.software.leanback": "tv", "android.software.xr.api.openxr": "xr",
                 "android.software.xr.api.spatial": "xr"}
TARGET_RULE = {"mobile": "target_sdk.mobile.min", "tablet": "target_sdk.mobile.min", "phone": "target_sdk.mobile.min",
               "wear": "target_sdk.wear.min", "automotive": "target_sdk.automotive.min", "auto": "target_sdk.automotive.min",
               "tv": "target_sdk.tv.min", "xr": "target_sdk.xr.min"}
POLICY = "https://support.google.com/googleplay/android-developer/answer/"


@dataclasses.dataclass
class Finding:
    id: str
    severity: str
    klass: str
    message: str
    fix: str = ""
    source: str = ""
    evidence: str = ""
    blocking: bool = False
    waived: dict | None = None

    def counts(self, strict=False):
        if self.waived:
            return False
        return self.severity == "BLOCKER" or self.blocking or (strict and self.severity == "LIKELY")


# ------------------------------------------------------------------------------------------------ inputs
def _yaml(path):
    return build.load_yaml(path)


def _json(path):
    p = pathlib.Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _date(s):
    return dt.date.fromisoformat(str(s)) if s else None


class Context:
    def __init__(self, kit, rec: build.ArtifactRecord, today: dt.date, project=None):
        kit = pathlib.Path(kit)
        self.kit, self.rec, self.today, self.project = kit, rec, today, project
        self.facts = _yaml(kit / "facts.yaml")
        self.console = _yaml(kit / "console.yaml")
        self.live = _json(kit / "console-live.json")
        self.rules = rules_mod.load()
        ext = _yaml(kit / "policy-extensions.yaml")
        self.extensions = ext.get("extensions", ext) if isinstance(ext, dict) else (ext or [])
        dec = _yaml(kit / "precheck-decisions.yaml")
        self.decisions = dec.get("decisions", dec) if isinstance(dec, dict) else (dec or [])
        self.history = _yaml(kit / "releases" / "release-history.yaml")
        self.testing = _yaml(kit / "testing-evidence.yaml")

    def rule(self, rid):
        return rules_mod.value(self.rules, rid)

    def src(self, rid):
        return self.rules[rid]["source_url"]

    def decl(self, name):
        return (self.facts.get("declarations") or {}).get(name)

    def sdk(self, prefixes):
        return sorted(p for p in prefixes if p in self.rec.dex_strings)

    def extension_until(self, kind):
        """Latest `until` of a Console-observed extension of this kind."""
        dates = [_date(e.get("until")) for e in self.extensions or []
                 if isinstance(e, dict) and e.get("kind") == kind and e.get("source") == "console"]
        dates = [d for d in dates if d]
        return max(dates) if dates else None


def _sdk_names(prefixes):
    return ", ".join(p[1:].rstrip("/").replace("/", ".") for p in prefixes)


# ------------------------------------------------------------------------------------------------ rules
def form_factors(ctx) -> list[str]:
    declared = ctx.facts.get("target_devices")
    if declared:
        return sorted({d for d in declared})
    found = {ff for name, ff in FORM_FEATURES.items() if ctx.rec.features.get(name)}
    return sorted(found) or ["mobile"]


def check_artifact(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    if not rec.package or rec.version_code is None:
        out.append(Finding("A-ARTIFACT", "BLOCKER", "artifact", "the bundle has no readable package or versionCode",
                           "rebuild the release bundle", evidence=rec.path))
    pkg = ctx.facts.get("package") or ctx.console.get("package")
    if pkg and rec.package and pkg != rec.package:
        out.append(Finding("A-ARTIFACT", "BLOCKER", "artifact",
                           f"bundle package {rec.package} differs from the kit's package {pkg}",
                           "build the right module or fix facts.yaml", evidence=rec.path))
    if rec.debuggable:
        out.append(Finding("A-ARTIFACT", "BLOCKER", "artifact", "android:debuggable=\"true\" in a release bundle",
                           "build the release variant", evidence="merged manifest"))
    return out


def check_target(ctx) -> list[Finding]:
    rec = ctx.rec
    if rec.target_sdk is None:
        return [Finding("B-TARGET", "BLOCKER", "artifact", "targetSdkVersion not found in the merged manifest",
                        "set targetSdk in the module's build file", ctx.src("target_sdk.mobile.min"))]
    out = []
    effective = _date(ctx.rules["target_sdk.mobile.min"]["effective_from"])
    for ff in form_factors(ctx):
        rid = TARGET_RULE.get(ff, "target_sdk.mobile.min")
        need = ctx.rule(rid)
        if rec.target_sdk >= need:
            continue
        until = ctx.extension_until("target_sdk")
        if ctx.today < effective or (until and ctx.today <= until):
            why = f"extension until {until}" if until and ctx.today >= effective else f"required from {effective}"
            out.append(Finding("B-TARGET", "WARNING", "artifact",
                               f"targetSdk {rec.target_sdk} is below {need} for {ff} ({why})",
                               f"raise targetSdk to {need}", ctx.src(rid)))
        else:
            out.append(Finding("B-TARGET", "BLOCKER", "artifact",
                               f"targetSdk {rec.target_sdk} is below {need}, required for new apps and updates on {ff} "
                               f"since {effective}", f"raise targetSdk to {need}; an extension counts only when "
                               "recorded from Policy status in policy-extensions.yaml", ctx.src(rid)))
    return out


def check_native(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    if not rec.native_libs:
        return out
    abis = set(rec.abis)
    if abis & {"armeabi-v7a", "armeabi"} and "arm64-v8a" not in abis:
        out.append(Finding("B-64BIT", "BLOCKER", "artifact", f"native code for {sorted(abis)} without arm64-v8a",
                           "add arm64-v8a to abiFilters / ndk config", POLICY + "9859372"))
    if "x86" in abis and "x86_64" not in abis:
        out.append(Finding("B-64BIT", "BLOCKER", "artifact", "native code for x86 without x86_64",
                           "add x86_64 or drop x86", POLICY + "9859372"))
    bad = [l for l in rec.native_libs if l.abi in build.ABIS_64 and l.bad_segments]
    unreadable = [l for l in rec.native_libs if l.abi in build.ABIS_64 and l.min_load_align is None]
    if bad:
        enforce = _date(ctx.rule("page_size.update_enforcement"))
        sev = "BLOCKER" if (rec.target_sdk or 0) >= 35 or ctx.today >= enforce else "WARNING"
        names = ", ".join(sorted({l.path for l in bad})[:8])
        out.append(Finding("B-16K", sev, "artifact",
                           f"{len(bad)} 64-bit native librar{'y' if len(bad) == 1 else 'ies'} with a PT_LOAD segment "
                           f"aligned below 16 KB: {names}",
                           "rebuild with NDK r28+ (16 KB by default) or link with -Wl,-z,max-page-size=16384; update "
                           "third-party SDKs that ship .so files; AGP 8.5.1+ for uncompressed packaging",
                           ctx.src("page_size.alignment_bytes"), evidence="ELF program headers"))
    for l in unreadable:
        out.append(Finding("B-16K", "WARNING", "artifact", f"could not read {l.path}: {l.note}", "inspect by hand",
                           ctx.src("page_size.alignment_bytes")))
    if rec.page_alignment_16k_config is False:
        out.append(Finding("B-16K", "INFO", "artifact", "bundle config does not declare PAGE_ALIGNMENT_16K "
                           "(packaging intent only; the ELF check above decides)"))
    return out


def check_signer(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    if not rec.signer_sha256:
        out.append(Finding("B-SIGN", "BLOCKER", "artifact", "the bundle is not signed", "sign it with the upload key",
                           POLICY + "9842756"))
        return out
    if "Android Debug" in (rec.signer_subject or ""):
        out.append(Finding("B-SIGN", "BLOCKER", "artifact", "signed with the Android debug certificate",
                           "sign with the upload key (signingConfigs.release)", POLICY + "9842756",
                           evidence=rec.signer_subject))
    want = (ctx.console.get("upload_certificate_sha256") or "").replace(":", "").lower()
    if want and want != rec.signer_sha256.lower():
        out.append(Finding("B-SIGN", "BLOCKER", "artifact",
                           f"signer {rec.signer_sha256[:16]}… is not the Console upload certificate {want[:16]}…",
                           "sign with the registered upload key, or reset the upload key in App integrity (person)",
                           POLICY + "9842756"))
    elif not want:
        out.append(Finding("B-SIGN", "WARNING", "console", "the Console upload certificate is not recorded in "
                           "console.yaml, so the signer cannot be compared", "read it from App integrity "
                           "(console_guide.py KIT bootstrap)", POLICY + "9842756"))
    return out


def check_secrets_tracked(ctx) -> list[Finding]:
    if not ctx.project:
        return []
    try:
        files = subprocess.run(["git", "ls-files"], cwd=ctx.project, capture_output=True, text=True,
                               timeout=30).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return []
    hits = [f for f in files if f.endswith((".jks", ".keystore")) or f.endswith("keystore.properties")]
    if hits:
        return [Finding("B-SIGN", "BLOCKER", "project", f"signing material is tracked in git: {', '.join(hits[:5])}",
                        "git rm --cached the files, add them to .gitignore, and treat the key as exposed")]
    return []


def check_version(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    if rec.version_code is None:
        return out
    mx = ctx.rule("version_code.max")
    if rec.version_code > mx:
        out.append(Finding("B-VERSION", "BLOCKER", "artifact", f"versionCode {rec.version_code} is above the maximum "
                           f"{mx:,}", "lower the versionCode scheme", ctx.src("version_code.max")))
    seen = []
    for t in (ctx.live.get("tracks") or {}).values():
        for r in t.get("releases", []):
            seen += [int(v) for v in r.get("versionCodes", [])]
    for h in (ctx.history.get("releases") or []) if isinstance(ctx.history, dict) else []:
        if h.get("version_code") is not None:
            seen.append(int(h["version_code"]))
    if not ctx.live.get("tracks"):
        out.append(Finding("B-VERSION", "WARNING", "remote", "no remote snapshot: the highest version code on Play is "
                           "unknown", "run release.py import KIT first"))
    if seen and rec.version_code <= max(seen):
        out.append(Finding("B-VERSION", "BLOCKER", "remote", f"versionCode {rec.version_code} is not above "
                           f"{max(seen)}, already used on Play or in local history", "bump: build.py bump --kit KIT",
                           evidence=f"snapshot {ctx.live.get('captured_at', '?')}"))
    return out


def check_size(ctx) -> list[Finding]:
    size = ctx.rec.compressed_download_bytes
    if not size:
        return [Finding("B-SIZE", "INFO", "artifact", "compressed download size not measured (run with --size; the raw "
                        "AAB size is not the download size)", source=ctx.src("size.base_module.max_mb"))]
    mb = size["max"] / (1024 * 1024)
    limit, warn = ctx.rule("size.base_module.max_mb"), ctx.rule("size.mobile_data_warning_mb")
    if mb > limit:
        return [Finding("B-SIZE", "BLOCKER", "artifact", f"largest device download is {mb:.0f} MB, over the {limit} MB "
                        "base-module limit", "move assets to asset packs (Play Asset Delivery)",
                        ctx.src("size.base_module.max_mb"))]
    if mb > warn:
        return [Finding("B-SIZE", "WARNING", "artifact", f"largest device download is {mb:.0f} MB: Play asks before "
                        f"installing over mobile data above {warn} MB", "trim or use asset packs",
                        ctx.src("size.mobile_data_warning_mb"))]
    return []


def _major(v):
    try:
        return int(str(v).split(".")[0])
    except ValueError:
        return None


def check_billing(ctx) -> list[Finding]:
    rec = ctx.rec
    version = rec.meta_data.get("com.google.android.play.billingclient.version")
    uses = BILLING_SDK in rec.dex_strings or "com.android.vending.BILLING" in rec.permissions or version
    if not uses:
        return []
    src = ctx.src("billing_library.min_major")
    major = _major(version) if version else None
    if major is None:
        return [Finding("B-BILLING-LIB", "QUESTION", "inference", "Play Billing is packaged but its version could not "
                        "be read (manifest merging can drop the meta-data)",
                        "check `./gradlew :app:dependencies` for com.android.billingclient:billing", src)]
    need = ctx.rule("billing_library.min_major")
    deadline = _date(ctx.rules["billing_library.min_major"]["effective_from"])
    if major < need:
        until = ctx.extension_until("billing_library")
        if ctx.today < deadline or (until and ctx.today <= until):
            return [Finding("B-BILLING-LIB", "WARNING", "artifact", f"Billing Library {version}: version {need}+ needed "
                            f"(deadline {deadline}{', extension until ' + str(until) if until else ''})",
                            f"upgrade to com.android.billingclient:billing {need}.x", src)]
        return [Finding("B-BILLING-LIB", "BLOCKER", "artifact", f"Billing Library {version}: new apps and updates "
                        f"need version {need} or later since {deadline}", f"upgrade to billing {need}.x; an extension "
                        "counts only when recorded from Policy status", src)]
    nxt = ctx.rule("billing_library.next_deadline")
    if major < need + 1:
        return [Finding("B-BILLING-LIB", "WARNING", "artifact", f"Billing Library {version} is deprecated on {nxt}",
                        f"plan the upgrade to {need + 1}.x", src)]
    return []


def check_policy(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    pay = ctx.sdk(PAYMENT_SDKS)
    if pay:
        dg = ctx.decl("digital_goods")
        if dg is True:
            out.append(Finding("R-PAY", "LIKELY", "inference", f"payment SDK ({_sdk_names(pay)}) in an app that sells "
                               "digital goods: those must go through Play Billing (or an approved alternative)",
                               "use Play Billing for digital goods; keep the SDK for physical goods only",
                               POLICY + "10281818"))
        elif dg is None:
            out.append(Finding("R-PAY", "QUESTION", "inference", f"payment SDK ({_sdk_names(pay)}) present: does the "
                               "app sell digital goods or subscriptions?", "answer declarations.digital_goods in "
                               "facts.yaml", POLICY + "10281818"))
    forms = {}
    for perm in rec.permissions:
        if perm in SENSITIVE_PERMISSIONS:
            forms.setdefault(SENSITIVE_PERMISSIONS[perm], []).append(perm.rsplit(".", 1)[1])
        elif perm.startswith(FGS_PREFIX):
            forms.setdefault("Foreground service types declaration", []).append(perm.rsplit(".", 1)[1])
    for form, perms in sorted(forms.items()):
        out.append(Finding("R-PERM", "QUESTION", "artifact", f"{', '.join(perms)}: needs the Console \"{form}\" with a "
                           "core-use justification and often a video", "fill the declaration in App content → "
                           "Sensitive permissions, or remove the permission", POLICY + "9888170"))
    for perm, text in DATED_PERMISSION_CHANGES.items():
        if perm in rec.permissions:
            out.append(Finding("R-PERM", "WARNING", "artifact", text, "review the policy change before that date",
                               POLICY + "17134731"))
    ads = ctx.sdk(ADS_SDKS)
    if ads and (rec.target_sdk or 0) >= 33 and "com.google.android.gms.permission.AD_ID" not in rec.permissions:
        out.append(Finding("R-ADID", "QUESTION", "inference", f"ads/analytics SDK ({_sdk_names(ads)}) without the "
                           "AD_ID permission in the merged manifest: does the app use the advertising ID?",
                           "declare it in App content → Advertising ID and in Data safety; add the permission if used",
                           POLICY + "6048248"))
    if not ctx.facts.get("privacy_policy_url"):
        out.append(Finding("R-PRIVACY", "QUESTION", "question", "no privacy_policy_url in facts.yaml (the App content "
                           "form cannot be submitted without one)", "publish a privacy policy page and add its URL",
                           POLICY + "9888076", blocking=True))
    acct = ctx.sdk(ACCOUNT_SDKS)
    if (acct or ctx.decl("account_creation")) and not ctx.facts.get("account_deletion_url"):
        why = f"an account SDK is packaged ({_sdk_names(acct)})" if acct else "facts.yaml says people create accounts"
        out.append(Finding("R-PRIVACY", "QUESTION", "inference" if acct else "facts",
                           f"{why} but facts.yaml has no account_deletion_url (if people can create accounts, Play "
                           "requires a deletion path)",
                           "offer in-app deletion and a web deletion page; add its URL", POLICY + "13327111",
                           blocking=True))
    collecting = ctx.sdk(COLLECTING_SDKS)
    ds = ctx.kit / "datasafety.yaml"
    if collecting and not ds.exists():
        out.append(Finding("R-DATASAFETY", "WARNING", "inference", f"SDKs that collect data are packaged "
                           f"({_sdk_names(collecting)}); make sure the Data safety form covers what each collects and "
                           "shares (on-device-only and end-to-end-encrypted data are out of scope)",
                           "answer the Data safety form (datasafety.yaml arrives in 1.1)", POLICY + "10787469"))
    elif collecting and ds.exists() and (_yaml(ds).get("collects_nothing") is True):
        out.append(Finding("R-DATASAFETY", "LIKELY", "inference", f"Data safety says nothing is collected but "
                           f"{_sdk_names(collecting)} is packaged", "correct the declaration", POLICY + "10787469"))
    out += check_genai(ctx)
    for decl, rid in REGULATED.items():
        if ctx.decl(decl) is None:
            out.append(Finding(rid, "QUESTION", "question", f"declarations.{decl} is unanswered in facts.yaml",
                               "answer it; the App content forms ask", POLICY + "9859455"))
    return out


def check_genai(ctx) -> list[Finding]:
    out, genai = [], ctx.decl("genai")
    hints = ctx.sdk(GENAI_SDKS)
    src = POLICY + "13985936"
    if genai is None and hints:
        return [Finding("R-GENAI", "QUESTION", "inference", f"generative-AI SDK ({_sdk_names(hints)}) present: does the "
                        "app generate content with AI?", "answer declarations.genai in facts.yaml", src)]
    if genai is not True:
        return out
    g = ctx.facts.get("genai") or {}
    if not g.get("reporting_entry_point"):
        out.append(Finding("R-GENAI", "QUESTION", "question", "where in the app can people report or flag offensive AI "
                           "output? (required in-app reporting)", "add facts.genai.reporting_entry_point with the "
                           "screen and string id", src))
    access = _yaml(ctx.kit / "private" / "app-access.yaml")
    if not access.get("accounts"):
        out.append(Finding("R-GENAI", "BLOCKER", "facts", "no reviewer accounts in private/app-access.yaml: reviewers "
                           "need full access to every AI feature in every region, past paywalls and 2FA",
                           "add accounts (they go into App access, never into git)", POLICY + "15748846"))
    if not (ctx.kit / "private" / "genai-safety-tests.md").exists():
        out.append(Finding("R-GENAI", "LIKELY", "facts", "no documented safety tests (NCII / nudify / deepfake / CSAE / "
                           "deception refusals with dates and model versions) in private/genai-safety-tests.md",
                           "run and record them; Google asks for them on review", src))
    if not g.get("ad_creatives_reviewed"):
        out.append(Finding("R-GENAI", "QUESTION", "question", "have the app's ad creatives been reviewed under the AI "
                           "content policy?", "set facts.genai.ad_creatives_reviewed", src))
    if not g.get("data_safety_reflects_prompts"):
        out.append(Finding("R-GENAI", "QUESTION", "question", "are prompts and uploads reflected in Data safety and the "
                           "content rating?", "set facts.genai.data_safety_reflects_prompts", src))
    out.append(Finding("R-GENAI", "INFO", "facts", "expect a longer review for AI-content apps"))
    return out


def check_account(ctx) -> list[Finding]:
    out = []
    acct = ctx.facts.get("account") or {}
    if acct.get("type") == "personal":
        for d in ORG_REQUIRED:
            if ctx.decl(d):
                out.append(Finding("W-ORG", "BLOCKER", "facts", f"{d} apps need an organization developer account",
                                   "publish from an organization account", POLICY + "13634885"))
        after = _date(ctx.rule("testing_gate.accounts_created_after"))
        if acct.get("created_after_2023_11_13") or (_date(acct.get("created")) and _date(acct["created"]) > after):
            granted = any((d or {}).get("outcome") == "granted" for d in (ctx.testing.get("decisions") or []))
            if not granted:
                out.append(Finding("W-TESTING-GATE", "BLOCKER", "console",
                                   f"personal account created after {after}: production needs {ctx.rule('testing_gate.testers')} "
                                   f"testers opted in for {ctx.rule('testing_gate.days')} days and Google's approval "
                                   "(blocks production only)", "testing_evidence.py add … then apply in the Console",
                                   ctx.src("testing_gate.testers")))
    if not (ctx.live.get("developer_verification") or {}).get("observed_at"):
        out.append(Finding("W-VERIFY", "WARNING", "console", "developer verification / Play package registration "
                           "status is not recorded", "read it on Play Console Home (console_guide.py)",
                           "https://developer.android.com/developer-verification"))
    return out


def check_quality(ctx) -> list[Finding]:
    rec, out = ctx.rec, []
    locales = ctx.facts.get("locales") or []
    if len(locales) > 1 and not rec.locale_config:
        out.append(Finding("W-LOCALE", "WARNING", "artifact", f"{len(locales)} store locales but no "
                           "android:localeConfig (per-app language settings)",
                           "generateLocaleConfig = true in androidResources",
                           "https://developer.android.com/guide/topics/resources/app-languages"))
    game = ctx.facts.get("category_type") == "game"
    limit = ctx.rule("dex.game.threshold_mb" if game else "dex.app.threshold_mb")
    if rec.dex_total_bytes > limit * 1024 * 1024 and not rec.mapping_path:
        out.append(Finding("W-DEX", "WARNING", "artifact", f"DEX is {rec.dex_total_bytes / 1048576:.1f} MB with no "
                           f"mapping file: from {ctx.rules['dex.app.threshold_mb']['effective_from']} Play expects ≥25 % "
                           "obfuscation, optimization and shrinking above "
                           f"{limit} MB", "enable R8 (isMinifyEnabled = true) and upload mapping.txt",
                           ctx.src("dex.app.threshold_mb")))
    if ctx.sdk(SIGNIN_SDKS):
        out.append(Finding("W-RESTORE", "INFO", "inference", "sign-in SDK present: zero-tap sign-in restoration "
                           "(Restore Credentials) is expected from April 2027", source=POLICY + "17492799"))
    return out


CHECKS = (check_artifact, check_target, check_native, check_signer, check_secrets_tracked, check_version,
          check_size, check_billing, check_policy, check_account, check_quality)


def apply_waivers(ctx, findings):
    for d in ctx.decisions or []:
        if not isinstance(d, dict):
            continue
        missing = [k for k in ("rule", "reason", "date", "decided_by") if not d.get(k)]
        for f in findings:
            if f.id != d.get("rule") or f.waived:
                continue
            if missing:
                f.message += f" (waiver ignored: missing {', '.join(missing)})"
            elif f.klass == "artifact" and f.severity == "BLOCKER":
                f.message += " (waiver refused: artifact BLOCKERs cannot be waived)"
            elif f.klass in ("inference", "question", "facts"):
                f.waived = {k: d[k] for k in ("reason", "date", "decided_by")}
    return findings


def run(kit, rec, today=None, project=None) -> list[Finding]:
    ctx = Context(kit, rec, today or dt.date.today(), project)
    findings = []
    for check in CHECKS:
        findings += check(ctx)
    order = {s: i for i, s in enumerate(SEVERITIES)}
    findings.sort(key=lambda f: (order[f.severity], f.id))
    return apply_waivers(ctx, findings)


def render(rec, findings, today) -> str:
    icon = {"BLOCKER": "🔴", "LIKELY": "🟠", "WARNING": "🟡", "QUESTION": "❓", "INFO": "ℹ️"}
    lines = [f"# Pre-check — {rec.package or '?'} {rec.version_name or ''} ({rec.version_code})", "",
             f"Checked {today} · AAB sha256 `{rec.sha256[:16]}…` · targetSdk {rec.target_sdk} · minSdk {rec.min_sdk}", ""]
    counts = {s: sum(1 for f in findings if f.severity == s and not f.waived) for s in SEVERITIES}
    lines.append(" · ".join(f"{icon[s]} {counts[s]} {s.lower()}" for s in SEVERITIES))
    lines.append("")
    lines.append("| | Rule | Finding | Evidence | Fix | Source |")
    lines.append("|---|---|---|---|---|---|")
    for f in findings:
        mark = "✔︎ waived" if f.waived else icon[f.severity] + (" blocking" if f.blocking else "")
        msg = f.message.replace("|", "\\|") + (f" — waived: {f.waived['reason']} ({f.waived['date']})" if f.waived else "")
        lines.append(f"| {mark} | {f.id} | {msg} | {f.klass} | {f.fix.replace('|', '/')} | {f.source} |")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("project", nargs="?")
    p.add_argument("--aab")
    p.add_argument("--kit", default="./play-store-kit")
    p.add_argument("--size", action="store_true")
    p.add_argument("--mapping")
    p.add_argument("--today")
    p.add_argument("--json", action="store_true")
    p.add_argument("--strict", action="store_true")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args(argv)
    args.kit = str(pathlib.Path(args.kit).resolve())
    if args.project:
        args.project = str(pathlib.Path(args.project).resolve())
    today = _date(args.today) or dt.date.today()
    try:
        aab = args.aab
        if not aab:
            if not args.project:
                p.error("give --aab PATH or a PROJECT directory")
            plan = build.adapter_plan(args.kit)
            aab = str(build.select_output(plan["root"], plan["output_glob"] or "**/*.aab"))
        mapping = args.mapping or build.kit_symbol_paths(args.kit).get("mapping_path")   # project.yaml fallback
        if mapping and not args.mapping and not pathlib.Path(mapping).exists():
            mapping = None
        rec = build.inspect(aab, size=args.size, mapping=mapping)
    except build.BuildError as e:
        print(e, file=sys.stderr)
        return 1
    findings = run(args.kit, rec, today, args.project)
    if args.json:
        print(json.dumps([dataclasses.asdict(f) for f in findings], indent=2, ensure_ascii=False))
    else:
        text = render(rec, findings, today)
        if not args.dry_run and pathlib.Path(args.kit).is_dir():
            (pathlib.Path(args.kit) / "PRECHECK.md").write_text(text, encoding="utf-8")
        print(text)
    return 1 if any(f.counts(args.strict) for f in findings) else 0


if __name__ == "__main__":
    import deps
    deps.require()
    sys.exit(main())
