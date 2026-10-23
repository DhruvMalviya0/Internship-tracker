"""Detection for Google Docs / external forms and human verification (CAPTCHA / bot challenges)."""
from __future__ import annotations

import re
from typing import NamedTuple


class VerificationResult(NamedTuple):
    detected: bool
    kind: str  # "google_docs", "captcha", "login", ""
    reason: str
    detail: str = ""


# Links or patterns indicating Google Docs / Forms / Sheets / external documents
GOOGLE_DOC_PATTERNS = [
    re.compile(r"https?://(?:docs|drive)\.google\.com/[^\s\"'>]+", re.I),
    re.compile(r"https?://forms\.gle/[^\s\"'>]+", re.I),
    re.compile(r"https?://(?:[a-z0-9-]+\.)?typeform\.com/[^\s\"'>]+", re.I),
    re.compile(r"https?://(?:[a-z0-9-]+\.)?airtable\.com/[^\s\"'>]+", re.I),
    re.compile(r"https?://forms\.office\.com/[^\s\"'>]+", re.I),
]

GOOGLE_DOC_PHRASES = [
    re.compile(r"\bgoogle\s+(?:doc|docs|form|forms|sheet|sheets)\b", re.I),
    re.compile(r"\b(?:fill\s+(?:out|in|up)|submit|complete)\s+(?:this|the|our|a)?\s*(?:separate\s+)?(?:google\s+)?(?:form|doc|document|sheet|assignment)\b", re.I),
    re.compile(r"\bsubmit\s+(?:your\s+)?(?:details|application|cv|resume|assignment)\s+(?:in|on|at|via|through)\s+(?:a\s+)?(?:separate\s+)?(?:google\s+)?(?:doc|form|link|sheet)\b", re.I),
    re.compile(r"\bassignment\s+(?:doc|form|link|submission|sheet)\b", re.I),
    re.compile(r"\b(?:application|intake)\s+form\s*:\s*https?://", re.I),
]

# Patterns indicating human verification / CAPTCHAs / challenges
CAPTCHA_PATTERNS = [
    re.compile(r"challenges\.cloudflare\.com", re.I),
    re.compile(r"cf-turnstile|turnstile", re.I),
    re.compile(r"google\.com/recaptcha|g-recaptcha|recaptcha/api\.js|grecaptcha", re.I),
    re.compile(r"hcaptcha\.com|h-captcha", re.I),
    re.compile(r"arkoselabs|funcaptcha", re.I),
    re.compile(r"awswaf|aws-waf-captcha", re.I),
    re.compile(r"\bbotdetect\b|\bcaptcha\b", re.I),
]

CHALLENGE_PHRASES = [
    re.compile(r"verify\s+(?:that\s+)?you\s+are\s+human", re.I),
    re.compile(r"attention\s+required!\s*\|\s*cloudflare", re.I),
    re.compile(r"checking\s+your\s+browser\s+before\s+accessing", re.I),
    re.compile(r"security\s+check\s+to\s+access", re.I),
    re.compile(r"please\s+complete\s+the\s+captcha", re.I),
    re.compile(r"press\s+&\s+hold\s+to\s+confirm\s+you're\s+human", re.I),
]

LOGIN_REQUIRED_PLATFORMS = {
    "internshala": "Internshala requires candidate account login and session verification",
    "unstop": "Unstop requires candidate account login and student verification",
    "wellfound": "Wellfound requires candidate account login and bot check",
    "linkedin": "LinkedIn requires candidate sign-in",
    "handshake": "Handshake requires university student login",
    "cuvette": "Cuvette requires student sign-in",
    "himalayas": "Himalayas applies require external company redirect or Himalayas account",
    "peerlist": "Peerlist requires Peerlist profile login to apply",
}


def check_google_docs(text: str = "", url: str = "", fields: list[dict] | None = None) -> VerificationResult:
    """Checks whether the posting or application requires submitting details in a Google Doc / Form / Sheet."""
    combined = f"{text}\n{url}"
    for pat in GOOGLE_DOC_PATTERNS:
        m = pat.search(combined)
        if m:
            return VerificationResult(
                detected=True,
                kind="google_docs",
                reason=f"Google Docs / external form link detected: {m.group(0)[:80]}",
                detail=m.group(0),
            )
    for pat in GOOGLE_DOC_PHRASES:
        m = pat.search(text)
        if m:
            return VerificationResult(
                detected=True,
                kind="google_docs",
                reason=f"Requires details in Google Doc/Form: “{m.group(0)}”",
                detail=m.group(0),
            )
    if fields:
        for f in fields:
            label = str(f.get("label") or f.get("name") or "")
            for pat in GOOGLE_DOC_PHRASES:
                if pat.search(label):
                    return VerificationResult(
                        detected=True,
                        kind="google_docs",
                        reason=f"Application question asks for Google Doc/Form: “{label[:60]}”",
                        detail=label,
                    )
    return VerificationResult(detected=False, kind="", reason="")


def check_human_verification(
    html_content: str = "",
    url: str = "",
    source: str = "",
    status_code: int = 200,
    headers: dict | None = None,
) -> VerificationResult:
    """Checks whether human verification (CAPTCHA, Cloudflare challenge, or account login) is required."""
    # 1. Platform-level login requirement
    src_key = source.lower().strip()
    if src_key in LOGIN_REQUIRED_PLATFORMS:
        return VerificationResult(
            detected=True,
            kind="login",
            reason=LOGIN_REQUIRED_PLATFORMS[src_key],
            detail=src_key,
        )

    # 2. HTTP status challenge (403/429 with challenge indicators)
    if status_code in (403, 429):
        h_str = str(headers or {}).lower()
        if "cloudflare" in h_str or "captcha" in html_content.lower():
            return VerificationResult(
                detected=True,
                kind="captcha",
                reason=f"HTTP {status_code} bot challenge / Cloudflare protection detected",
                detail=f"status_{status_code}",
            )

    # 3. CAPTCHA script or widget in HTML
    for pat in CAPTCHA_PATTERNS:
        m = pat.search(html_content)
        if m:
            return VerificationResult(
                detected=True,
                kind="captcha",
                reason=f"Human verification widget detected ({m.group(0)})",
                detail=m.group(0),
            )

    # 4. Challenge phrasing in text
    for pat in CHALLENGE_PHRASES:
        m = pat.search(html_content)
        if m:
            return VerificationResult(
                detected=True,
                kind="captcha",
                reason=f"Bot challenge phrasing detected: “{m.group(0)}”",
                detail=m.group(0),
            )

    return VerificationResult(detected=False, kind="", reason="")
