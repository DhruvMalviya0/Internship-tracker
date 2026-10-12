"""Small text helpers shared by fetchers, filters and the web server."""
from __future__ import annotations

import hashlib
import html
import re
from html.parser import HTMLParser
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_BLOCK_TAGS = {
    "p", "div", "br", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6",
    "tr", "section", "article", "header", "footer", "blockquote", "table",
}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style"):
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style"):
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(markup: str, unescape_first: bool = False) -> str:
    """Strips tags and keeps paragraph/list breaks. Greenhouse double-escapes, hence unescape_first."""
    if not markup:
        return ""
    if unescape_first:
        markup = html.unescape(markup)
    parser = _TextExtractor()
    parser.feed(markup)
    parser.close()
    text = "".join(parser.parts)
    text = re.sub(r"[ \t\r\f\v\xa0​]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


_TRACKING_PARAMS = re.compile(r"^(utm_.*|gh_src|ref|source|src|lever-.*|fbclid|gclid)$", re.I)


def normalize_url(url: str) -> str:
    """Canonical form used as the de-duplication key: no tracking params, fragment, www or trailing slash."""
    if not url:
        return ""
    parts = urlsplit(url.strip())
    host = parts.netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    query = sorted((k, v) for k, v in parse_qsl(parts.query, keep_blank_values=False) if not _TRACKING_PARAMS.match(k))
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("https", host, path, urlencode(query), ""))


def sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


def compile_terms(terms) -> re.Pattern | None:
    """One case-insensitive regex matching any of the phrases as whole words."""
    cleaned = sorted({t.strip().lower() for t in terms if t and t.strip()}, key=len, reverse=True)
    if not cleaned:
        return None
    return re.compile(r"(?<![\w])(?:" + "|".join(re.escape(t) for t in cleaned) + r")(?![\w])", re.I)


def snippet(text: str, start: int, end: int, radius: int = 60) -> str:
    a, b = max(0, start - radius), min(len(text), end + radius)
    s = text[a:b].replace("\n", " ").strip()
    return ("..." if a > 0 else "") + s + ("..." if b < len(text) else "")
