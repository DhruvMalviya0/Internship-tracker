"""Gemini API client for answering job application questions and tailoring drafts.

Uses the Google Generative Language REST API directly with requests.
API key is read from the parameter, profile.json ('gemini.api_key' or 'gemini_api_key'),
or the GEMINI_API_KEY environment variable.
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any

import requests

log = logging.getLogger(__name__)

DEFAULT_MODEL = "gemini-1.5-flash"
API_BASE = "https://generativelanguage.googleapis.com/v1beta/models"
GROQ_API_BASE = "https://api.groq.com/openai/v1/chat/completions"
GROQ_DEFAULT_MODEL = "qwen/qwen3.8-27b"


def _is_groq_key(key: str) -> bool:
    return key.strip().startswith("gsk_")


def get_api_key(profile: dict | None = None, override: str | None = None) -> str:
    """Finds the Gemini/Groq API key from explicit override, profile, environment, or .env file."""
    if override and override.strip():
        return override.strip()
    if profile:
        k = (profile.get("gemini") or {}).get("api_key") or profile.get("gemini_api_key") or (profile.get("groq") or {}).get("api_key")
        if k and str(k).strip():
            return str(k).strip()
    k = os.environ.get("GEMINI_API_KEY", "").strip() or os.environ.get("GROQ_API_KEY", "").strip()
    if k:
        return k
    # Check .env file directly
    from . import config
    env_file = config.ROOT / ".env"
    if env_file.exists():
        try:
            with open(env_file, encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("#"):
                        continue
                    if line.startswith("GEMINI_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            return val
                    elif line.startswith("GROQ_API_KEY="):
                        val = line.split("=", 1)[1].strip()
                        if val:
                            return val
        except Exception:
            pass
    return ""


def get_model(profile: dict | None = None) -> str:
    """Returns the configured AI model name."""
    key = get_api_key(profile)
    if profile:
        m = (profile.get("gemini") or {}).get("model") or (profile.get("groq") or {}).get("model")
        if m and str(m).strip():
            return str(m).strip()
    env_m = os.environ.get("GEMINI_MODEL", "").strip() or os.environ.get("GROQ_MODEL", "").strip()
    if env_m:
        return env_m
    return GROQ_DEFAULT_MODEL if _is_groq_key(key) else DEFAULT_MODEL


def is_configured(profile: dict | None = None, override: str | None = None) -> bool:
    """True if a non-empty Gemini/Groq API key is available."""
    return bool(get_api_key(profile, override))


def test_key(api_key: str, model: str = DEFAULT_MODEL) -> tuple[bool, str]:
    """Tests if a Gemini or Groq API key is valid by sending a tiny prompt."""
    if not api_key or not api_key.strip():
        return False, "API key is empty."
    clean_key = api_key.strip()
    if _is_groq_key(clean_key):
        use_model = model if model and not model.startswith("gemini-") else GROQ_DEFAULT_MODEL
        headers = {"Authorization": f"Bearer {clean_key}", "Content-Type": "application/json"}
        payload = {
            "model": use_model,
            "messages": [{"role": "user", "content": "Reply with exactly the word OK"}],
            "max_tokens": 10,
        }
        try:
            r = requests.post(GROQ_API_BASE, json=payload, headers=headers, timeout=12)
            if r.status_code == 200:
                return True, f"Key verified successfully (Groq model: {use_model})"
            err = r.json().get("error", {}).get("message", r.text[:200])
            return False, f"Groq API error ({r.status_code}): {err}"
        except Exception as exc:
            return False, f"Connection failed: {exc}"

    url = f"{API_BASE}/{model}:generateContent?key={clean_key}"
    payload = {
        "contents": [{"parts": [{"text": "Reply with exactly the word OK"}]}],
        "generationConfig": {"temperature": 0.0, "maxOutputTokens": 10},
    }
    try:
        r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=12)
        if r.status_code == 200:
            return True, f"Key verified successfully (model: {model})"
        err = r.json().get("error", {}).get("message", r.text[:200])
        return False, f"Gemini API error ({r.status_code}): {err}"
    except Exception as exc:
        return False, f"Connection failed: {exc}"


def _candidate_summary(profile: dict) -> str:
    """Summarizes the candidate profile for Gemini system instructions."""
    contact = profile.get("contact", {})
    loc = profile.get("location", {})
    drafts = profile.get("drafts", {})
    filters_ = profile.get("filters", {})

    skills = [s.get("name") for s in profile.get("skills", []) if s.get("name")]
    projects = []
    for p in profile.get("projects", [])[:8]:
        if p.get("name"):
            summary = p.get("summary", "")
            tags = ", ".join(p.get("tags", []))
            url = p.get("url", "")
            projects.append(f"- {p['name']}{f' ({url})' if url else ''}: {summary} [Tech: {tags}]")

    education = []
    for e in profile.get("education", []):
        education.append(f"- {e.get('degree', '')} at {e.get('school', '')} ({e.get('period', '')}), {e.get('place', '')}")

    achievements = []
    for a in profile.get("achievements", []):
        achievements.append(f"- {a.get('title', '')} ({a.get('year', '')}): {a.get('detail', '')}")

    name = profile.get("name", "Dhruv Malviya")
    preferred = profile.get("preferred_name", "Leaf")

    return f"""CANDIDATE PROFILE:
Name: {name} (Preferred: {preferred})
Email: {contact.get('email', '')}
Phone: {contact.get('phone', '')}
Location: {loc.get('city', 'Pune')}, {loc.get('country', 'India')} (Timezone: {loc.get('timezone', 'IST, UTC+5:30')})
Work authorization: Based in India. Not authorized to work in the US/EU; available for remote internships worldwide (contractor / Indian entity arrangement).
GitHub: {contact.get('github', '')}
LinkedIn: {contact.get('linkedin', '')}
Portfolio: {contact.get('portfolio', '')}
Availability: {filters_.get('hours_min', 20)}-{filters_.get('hours_max', 35)} hours/week. Start date: {drafts.get('start_date', 'Immediately')}.
Night shift / US hours compatible: {bool(filters_.get('night_shift_ok', True))}
Expected stipend: {drafts.get('expected_stipend') or 'Competitive / standard stipend for remote internships'}

Top Skills:
{", ".join(skills[:25])}

Education:
{"".join(f"{e}\n" for e in education) or "Undergraduate student in Computer Science"}

Key Projects:
{"".join(f"{p}\n" for p in projects)}

Achievements:
{"".join(f"{a}\n" for a in achievements)}
"""


def _call_gemini(prompt: str, api_key: str, model: str = DEFAULT_MODEL, json_mode: bool = False) -> str:
    """Helper to execute a generation request to the Gemini or Groq API."""
    clean_key = api_key.strip()
    if _is_groq_key(clean_key):
        use_model = model if model and not model.startswith("gemini-") else GROQ_DEFAULT_MODEL
        headers = {"Authorization": f"Bearer {clean_key}", "Content-Type": "application/json"}
        payload = {
            "model": use_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.2,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        for attempt in range(2):
            r = requests.post(GROQ_API_BASE, json=payload, headers=headers, timeout=25)
            if r.status_code == 429 and attempt == 0:
                err_text = r.text
                wait_time = 10.0
                m = re.search(r"try again in ([\d\.]+)s", err_text)
                if m:
                    try:
                        wait_time = float(m.group(1)) + 0.5
                    except ValueError:
                        pass
                log.info("Groq rate limit reached; waiting %.1fs before retrying...", wait_time)
                time.sleep(min(15.0, wait_time))
                continue
            if r.status_code != 200:
                err = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("Content-Type", "").startswith("application/json") else r.text[:200]
                raise RuntimeError(f"Groq API returned HTTP {r.status_code}: {err}")
            data = r.json()
            choices = data.get("choices") or []
            if not choices:
                return ""
            return (choices[0].get("message") or {}).get("content", "").strip()

    url = f"{API_BASE}/{model}:generateContent?key={clean_key}"
    cfg: dict[str, Any] = {"temperature": 0.2, "maxOutputTokens": 2048}
    if json_mode:
        cfg["responseMimeType"] = "application/json"

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": cfg,
    }
    r = requests.post(url, json=payload, headers={"Content-Type": "application/json"}, timeout=25)
    if r.status_code != 200:
        err = r.json().get("error", {}).get("message", r.text[:200]) if r.headers.get("Content-Type", "").startswith("application/json") else r.text[:200]
        raise RuntimeError(f"Gemini API returned HTTP {r.status_code}: {err}")
    data = r.json()
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "".join(p.get("text", "") for p in parts).strip()


def answer_questions(
    questions: list[dict],
    job_info: dict,
    profile: dict,
    api_key: str | None = None,
) -> dict[str, str]:
    """Answers a list of application form questions using Gemini based on the candidate's profile.

    Each question in questions is expected to have:
      - 'id' or 'name': unique identifier
      - 'label': question prompt text
      - 'type': optional ('text', 'textarea', 'radio', 'select', 'checkbox', etc.)
      - 'options': optional list of selectable options
      - 'required': boolean
    """
    key = get_api_key(profile, api_key)
    if not key:
        raise ValueError("Gemini API key is required to generate answers automatically.")

    model = get_model(profile)
    candidate = _candidate_summary(profile)

    q_formatted = []
    for i, q in enumerate(questions):
        qid = q.get("id") or q.get("name") or str(i)
        label = q.get("label") or q.get("name") or ""
        opts = q.get("options") or []
        opts_str = f" [Allowed options: {json.dumps(opts)}]" if opts else ""
        q_formatted.append(f"{i+1}. [ID: {qid}] \"{label}\"{opts_str}")

    prompt = f"""{candidate}

JOB POSTING:
Company: {job_info.get('company', '')}
Role: {job_info.get('role') or job_info.get('title', '')}
Location: {job_info.get('location', 'Remote')}
Description snippet: {job_info.get('description', '')[:1000]}

TASK:
You are applying to this internship on behalf of the candidate.
Answer the following application form questions.

RULES:
1. Ground every answer in the candidate's actual profile (Dhruv Malviya / Leaf).
2. For multiple choice / dropdown questions with allowed options, your answer MUST match one of the allowed options EXACTLY.
3. For text questions asking "Why do you want to join?", write 2-3 genuine, concise sentences connecting Dhruv's relevant projects/skills to the company's work.
4. For questions about legal authorization/sponsorship, be honest: Authorized to work in India; available for remote contractor internships; not authorized in US/EU.
5. For technical experience/years, give accurate numbers based on university graduation (2024-2028, 2+ years of project experience).
6. For URLs/links, use the exact links provided in the candidate profile.
7. Return your response as a JSON object mapping each question ID to the answer string:
   {{"question_id": "answer text", ...}}

QUESTIONS:
{"\n".join(q_formatted)}
"""

    try:
        res = _call_gemini(prompt, key, model=model, json_mode=True)
        # Parse JSON
        parsed = json.loads(res)
        if isinstance(parsed, dict):
            return {str(k): str(v).strip() for k, v in parsed.items()}
    except Exception as exc:
        log.warning("JSON answer generation failed, falling back to text parsing: %s", exc)

    # Fallback to single-question answering
    answers = {}
    for q in questions:
        qid = str(q.get("id") or q.get("name") or "")
        label = q.get("label") or q.get("name") or ""
        opts = q.get("options") or []
        ans = answer_single_question(label, opts, job_info, profile, api_key=key)
        if qid:
            answers[qid] = ans
    return answers


def answer_single_question(
    question_text: str,
    options: list[str] | None,
    job_info: dict,
    profile: dict,
    api_key: str | None = None,
) -> str:
    """Answers a single application question using Gemini."""
    key = get_api_key(profile, api_key)
    if not key:
        return ""

    model = get_model(profile)
    candidate = _candidate_summary(profile)
    opts_clause = ""
    if options:
        opts_clause = f"\nAllowed options (respond with ONLY the chosen option exactly): {json.dumps(options)}"

    prompt = f"""{candidate}

JOB POSTING:
Company: {job_info.get('company', '')}
Role: {job_info.get('role') or job_info.get('title', '')}
Description: {job_info.get('description', '')[:2000]}

QUESTION:
"{question_text}"{opts_clause}

Respond directly with only the answer to this question. No conversational intro, no quotes, no markdown headers."""

    try:
        return _call_gemini(prompt, key, model=model, json_mode=False)
    except Exception as exc:
        log.error("Failed to answer question '%s': %s", question_text, exc)
        return ""


def generate_tailored_note_and_answers(
    job_info: dict,
    profile: dict,
    api_key: str | None = None,
) -> tuple[str, str]:
    """Generates an enhanced tailored note and prefilled answers block using Gemini."""
    key = get_api_key(profile, api_key)
    if not key:
        from . import notes
        # Fall back to heuristic templates
        return notes.build_note(profile, job_info, [], None, False), notes.build_answers(profile, job_info, None, "")

    model = get_model(profile)
    candidate = _candidate_summary(profile)

    prompt = f"""{candidate}

JOB POSTING:
Company: {job_info.get('company', '')}
Role: {job_info.get('role') or job_info.get('title', '')}
Description: {job_info.get('description', '')[:4000]}

TASK:
1. Write a tailored, authentic 3-paragraph application note/cover letter for this role from Dhruv Malviya (Leaf). Connect his specific portfolio projects and design/engineering craft to what the company builds.
2. Produce a prefilled answers block answering:
   - Full Name
   - Email
   - Location
   - Work Authorization
   - Availability
   - Why {job_info.get('company', 'this company')}? (2-3 compelling sentences)
   - Relevant Projects (name + link + 1-sentence relevance)
   - Expected Stipend

Return a JSON object with:
{{
  "note": "the tailored cover note",
  "answers": "the prefilled answers block formatted as 'Key : Value' lines"
}}"""

    try:
        res = _call_gemini(prompt, key, model=model, json_mode=True)
        data = json.loads(res)
        return data.get("note", ""), data.get("answers", "")
    except Exception as exc:
        log.warning("Gemini draft generation failed, using standard template: %s", exc)
        from . import notes
        return notes.build_note(profile, job_info, [], None, False), notes.build_answers(profile, job_info, None, "")


def score_fit(job_info: dict, profile: dict, api_key: str | None = None) -> int | None:
    """Uses Gemini to rate candidate fit 0-100 based on skills, projects, and requirements."""
    key = get_api_key(profile, api_key)
    if not key:
        return None
    model = get_model(profile)
    candidate = _candidate_summary(profile)
    prompt = f"""{candidate}

JOB POSTING:
Company: {job_info.get('company', '')}
Role: {job_info.get('role') or job_info.get('title', '')}
Description: {job_info.get('description', '')[:3000]}

Rate how well this candidate matches the job requirements on a scale from 0 to 100.
Respond with ONLY an integer between 0 and 100."""
    try:
        txt = _call_gemini(prompt, key, model=model).strip()
        m = re.search(r"\b(\d{1,3})\b", txt)
        if m:
            val = int(m.group(1))
            return max(0, min(100, val))
    except Exception as exc:
        log.warning("Gemini score_fit failed: %s", exc)
    return None
