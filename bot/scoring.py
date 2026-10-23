"""0-100 match score from keywords. 60% skill fit (your weighted skills vs. the skills the posting asks for),
40% title relevance. The breakdown is stored so the website can explain each score."""
from __future__ import annotations

import logging
import re
from functools import lru_cache

from .fetchers.base import Job

log = logging.getLogger(__name__)

# Skills postings commonly ask for. Used to list what you are missing; your own profile skills win on overlap.
KNOWN_SKILLS: dict[str, list[str]] = {
    "TypeScript": ["typescript", "ts"],
    "JavaScript": ["javascript", "js", "es6"],
    "HTML": ["html", "html5"],
    "CSS": ["css", "css3", "scss", "sass"],
    "Next.js": ["next.js", "nextjs"],
    "Vue": ["vue", "vue.js", "vuejs", "nuxt"],
    "Angular": ["angular"],
    "Svelte": ["svelte", "sveltekit"],
    "Node.js": ["node.js", "nodejs", "node"],
    "Redux": ["redux", "zustand"],
    "GraphQL": ["graphql"],
    "REST APIs": ["rest api", "rest apis", "restful"],
    "Testing": ["jest", "vitest", "cypress", "playwright", "unit testing", "testing library"],
    "Storybook": ["storybook"],
    "Accessibility": ["accessibility", "a11y", "wcag"],
    "Three.js": ["three.js", "threejs"],
    "WebGL": ["webgl", "webgpu"],
    "GSAP": ["gsap"],
    "Figma": ["figma"],
    "UI design": ["ui design", "visual design", "interface design"],
    "UX design": ["ux design", "ux research", "user research", "usability testing"],
    "Prototyping": ["prototyping", "prototype", "prototypes"],
    "Motion design": ["motion design", "animation", "animations", "after effects", "lottie", "rive"],
    "Blender": ["blender", "3d modeling", "3d modelling"],
    "Python": ["python"],
    "Java": ["java"],
    "Go": ["golang"],
    "Rust": ["rust"],
    "C++": ["c++"],
    "SQL": ["sql", "postgres", "postgresql", "mysql"],
    "Docker": ["docker", "kubernetes"],
    "AWS / Cloud": ["aws", "gcp", "azure"],
    "Machine learning": ["machine learning", "pytorch", "tensorflow", "deep learning", "llm", "llms"],
    "Swift / iOS": ["swift", "swiftui", "ios"],
    "Kotlin / Android": ["kotlin", "android"],
    "React Native": ["react native"],
    "Git": ["git", "github"],
}

_TITLE_STRONG = re.compile(
    r"\b(front[- ]?end|frontend|ui|ux|design engineer|designer|design|creative|web|3d|graphics|motion|"
    r"interaction|visual|react|javascript|typescript|developer experience|devrel|product engineer)\b",
    re.I,
)
_TITLE_PARTIAL = re.compile(r"\b(software|full[- ]?stack|engineer|engineering|developer|product|mobile|app)\b", re.I)
_TITLE_OFF = re.compile(
    r"\b(back[- ]?end|data|analytics|analyst|machine learning|ml|ai research|research scientist|security|"
    r"infrastructure|devops|sre|hardware|mechanical|electrical|finance|accounting|legal|sales|marketing|"
    r"recruit\w*|people|hr|operations|supply chain|customer|support|business|law|policy|partnerships?|"
    r"translat\w*|journalis\w*|editorial|writer|writing|content|social media|video|growth|communications|"
    r"audit|tax|admin\w*|project management|events?|fundrais\w*|teaching|tutor)\b",
    re.I,
)


def _alias_regex(aliases) -> re.Pattern:
    parts = sorted({a.lower() for a in aliases if a}, key=len, reverse=True)
    return re.compile(r"(?<![\w.+#-])(?:" + "|".join(re.escape(p) for p in parts) + r")(?![\w+#-])", re.I)


@lru_cache(maxsize=8)
def _compiled(profile_key: tuple) -> tuple[list, list]:
    mine = [(name, float(w), _alias_regex([name, *aliases])) for name, w, aliases in profile_key]
    my_terms = {name.lower() for name, _, _ in profile_key} | {a.lower() for _, _, al in profile_key for a in al}
    known = [
        (name, _alias_regex(aliases))  # aliases only: display names like "Go" are too ambiguous to match
        for name, aliases in KNOWN_SKILLS.items()
        if name.lower() not in my_terms and not ({a.lower() for a in aliases} & my_terms)
    ]
    return mine, known


def _profile_key(profile: dict) -> tuple:
    return tuple(
        (s["name"], s.get("weight", 1), tuple(s.get("aliases", [])))
        for s in profile.get("skills", [])
    )


def title_relevance(title: str) -> float:
    if _TITLE_STRONG.search(title):
        return 1.0
    if _TITLE_OFF.search(title):
        return 0.0
    if _TITLE_PARTIAL.search(title):
        return 0.5
    return 0.1  # no tech or design words in the title at all


def score(job: Job, profile: dict) -> tuple[int, list[str], list[str], dict]:
    """Returns (score, matched skills, missing skills, breakdown)."""
    mine, known = _compiled(_profile_key(profile))
    text = f"{job.title}\n{job.description}"
    matched = [(name, w) for name, w, rx in mine if rx.search(text)]
    missing = [name for name, rx in known if rx.search(text)]

    matched_w = sum(w for _, w in matched)
    missing_w = 1.5 * min(len(missing), 8)
    rel = title_relevance(job.title)
    # Postings that name no known skills get a neutral fit that leans on how relevant the title is.
    fit = matched_w / (matched_w + missing_w) if (matched_w + missing_w) else 0.2 + 0.3 * rel
    total = round(100 * (0.6 * fit + 0.4 * rel))
    breakdown = {
        "skill_fit": round(fit, 2),
        "title_relevance": rel,
        "matched_weight": matched_w,
        "missing_count": len(missing),
        "formula": "60% skill fit + 40% title relevance",
    }
    return max(0, min(100, total)), [n for n, _ in matched], missing, breakdown


def llm_rescore(job: Job, profile: dict) -> int | None:
    """Optional LLM scoring using Gemini when profile.llm_scoring.enabled is True."""
    from . import gemini
    if not gemini.is_configured(profile):
        return None
    try:
        j_dict = job.to_dict() if hasattr(job, "to_dict") else {
            "company": job.company, "role": job.title, "description": job.description, "location": job.location,
        }
        return gemini.score_fit(j_dict, profile)
    except Exception as exc:
        log.warning("Gemini rescoring failed: %s", exc)
        return None
