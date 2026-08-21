"""Web search aimed at engineering standards and manufacturer data.

Full ISO/ASTM PDFs are usually paywalled. This tool finds the *right document
and the publicly stated requirements*, cites the URL, and says when the body
text was not actually retrieved. Inventing clause numbers is worse than
admitting the standard is behind a login.
"""

from __future__ import annotations

import html as htmlmod
import logging
import re
from html.parser import HTMLParser
from typing import Any
from urllib.parse import parse_qs, quote_plus, unquote, urljoin, urlparse

import httpx

from cadfree.cots.catalog import HOBBY_VENDOR_DOMAINS

LOG = logging.getLogger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Standards bodies and government sources — ranked first.
BODY_DOMAINS = (
    "iso.org",
    "astm.org",
    "asme.org",
    "din.de",
    "beuth.de",
    "sae.org",
    "iec.ch",
    "nist.gov",
    "ieee.org",
    "ansi.org",
    "aisc.org",
    "aws.org",
    "api.org",
    "ampp.org",
    "nace.org",
    "asce.org",
    "nfpa.org",
    "ul.com",
    "ipc.org",
    "bsigroup.com",
    "jisc.go.jp",
    "everyspec.com",
    "assist.dla.mil",
    "quicksearch.dla.mil",
    "ntrs.nasa.gov",
    "faa.gov",
    "ecfr.gov",
    "iso.org",
)
# Manufacturer / materials pages — official for datasheets, not for codes.
VENDOR_DOMAINS = (
    "matweb.com",
    "makeitfrom.com",
    "matmatch.com",
    "prusa3d.com",
    "bambulab.com",
    "ultimaker.com",
    "markforged.com",
    "stratasys.com",
    "3dsystems.com",
    "formlabs.com",
    "eos.info",
    "renishaw.com",
    "basf.com",
    "sabic.com",
    "dupont.com",
)
HOBBY_DOMAINS = HOBBY_VENDOR_DOMAINS
OFFICIAL_DOMAINS = BODY_DOMAINS + VENDOR_DOMAINS + HOBBY_DOMAINS
SITE_BOOST = (
    " (site:iso.org OR site:astm.org OR site:asme.org OR site:nist.gov"
    " OR site:sae.org OR site:din.de OR site:iec.ch OR site:everyspec.com)"
)
PARTS_SITE_BOOST = (
    " (site:getfpv.com OR site:racedayquads.com OR site:store.tmotor.com"
    " OR site:hobbyking.com OR site:betafpv.com OR site:shop.iflight.com"
    " OR site:rotorriot.com OR site:newbeedrone.com)"
)

STANDARD_RE = re.compile(
    r"\b(?:"
    r"ISO(?:/IEC)?[\s-]?\d+(?:-\d+)*(?::\d{4})?"
    r"|ASTM\s+[A-Z]\d+(?:/\d+)?"
    r"|ASME\s+[A-Z]{1,3}\d*(?:[.-]\d+(?:\.\d+)*)?"
    r"|DIN\s+\d+"
    r"|EN\s+ISO[\s-]?\d+"
    r"|SAE\s+J?\d+"
    r"|MIL(?:-| )?STD(?:-| )?\d+[A-Z]?"
    r"|NAS\s?\d+"
    r"|IPC(?:-| )[A-Z0-9-]+"
    r"|NIST\s+[A-Z]+\s+\d+"
    r")\b",
    re.I,
)

_STRIP = {"script", "style", "nav", "header", "footer", "aside", "form", "noscript", "svg", "iframe"}


def extract_standard_ids(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in STANDARD_RE.finditer(text or ""):
        token = re.sub(r"\s+", " ", match.group(0)).strip()
        key = token.upper()
        if key not in seen:
            seen.add(key)
            found.append(token)
    return found


def _host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _host_in(host: str, domains: tuple[str, ...]) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def is_official(url: str) -> bool:
    return _host_in(_host(url), OFFICIAL_DOMAINS)


def source_kind(url: str) -> str:
    host = _host(url)
    if _host_in(host, BODY_DOMAINS):
        return "body"
    if _host_in(host, VENDOR_DOMAINS) or _host_in(host, HOBBY_DOMAINS):
        return "vendor"
    if host.endswith("wikipedia.org") or host == "wikipedia.org":
        return "encyclopedia"
    return "web"


def rewrite_query(query: str, *, intent: str = "standards") -> str:
    q = (query or "").strip()
    if not q:
        return q
    if intent == "datasheet":
        if not re.search(r"datasheet|filament|yield|tensile", q, re.I):
            q = q + " datasheet tensile yield"
        return q
    if intent == "machine":
        if not re.search(r"build volume|envelope|spec", q, re.I):
            q = q + " official specifications build volume"
        return q
    if intent == "parts":
        if not re.search(r"\b(buy|in stock|vendor|datasheet)\b", q, re.I):
            q = q + " buy"
        return q
    if STANDARD_RE.search(q):
        return q
    if not re.search(r"\b(iso|astm|asme|din|sae|mil-std|nas|ipc|nist)\b", q, re.I):
        q = f'{q} (ISO OR ASTM OR ASME OR DIN OR SAE OR "MIL-STD")'
    return q


_KIND_RANK = {"body": 0, "vendor": 1, "encyclopedia": 2, "web": 3}


def annotate_result(item: dict[str, str]) -> dict[str, Any]:
    url = item.get("url") or ""
    blob = f"{item.get('title', '')} {item.get('snippet', '')} {url}"
    kind = source_kind(url)
    return {
        **item,
        "official": kind in {"body", "vendor"},
        "source": kind,
        "standard_ids": extract_standard_ids(blob),
    }


def dedupe_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in results:
        key = (item.get("url") or "").split("#", 1)[0].rstrip("/").lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def rank_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple:
        kind = _KIND_RANK.get(item.get("source") or source_kind(item.get("url") or ""), 3)
        stds = 0 if item.get("standard_ids") else 1
        return (kind, stds)

    return sorted(results, key=key)


def _ddg_library(query: str, max_results: int) -> list[dict[str, str]]:
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore
        except ImportError:
            raise RuntimeError("no ddgs")
    with DDGS() as client:
        raw = list(client.text(query, max_results=max_results, region="wt-wt"))
    out = []
    for item in raw:
        url = item.get("href") or item.get("url") or ""
        if not url:
            continue
        out.append(
            {
                "title": item.get("title") or "",
                "url": url,
                "snippet": item.get("body") or item.get("snippet") or "",
            }
        )
    return out


class _DDGParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.results: list[dict[str, str]] = []
        self._in_title = False
        self._in_snippet = False
        self._current: dict[str, str] | None = None
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        ad = dict(attrs)
        cls = ad.get("class") or ""
        href = ad.get("href") or ""
        if tag == "a" and ("result__a" in cls or "result-link" in cls):
            url = href
            if "uddg=" in href:
                url = parse_qs(urlparse(href).query).get("uddg", [href])[0]
            self._current = {"title": "", "url": unquote(url), "snippet": ""}
            self._in_title = True
            self._buf = []
        elif tag in {"a", "div"} and self._current and ("result__snippet" in cls or "result-snippet" in cls):
            self._in_snippet = True
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._in_title and tag == "a" and self._current is not None:
            self._current["title"] = htmlmod.unescape("".join(self._buf)).strip()
            self._in_title = False
        if self._in_snippet and tag in {"a", "div"} and self._current is not None:
            self._current["snippet"] = htmlmod.unescape("".join(self._buf)).strip()
            self._in_snippet = False
            if self._current.get("url"):
                self.results.append(self._current)
            self._current = None

    def handle_data(self, data: str) -> None:
        if self._in_title or self._in_snippet:
            self._buf.append(data)


def _ddg_html(query: str, max_results: int) -> list[dict[str, str]]:
    url = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    with httpx.Client(timeout=20.0, headers=HEADERS, follow_redirects=True) as client:
        resp = client.get(url)
        resp.raise_for_status()
        parser = _DDGParser()
        parser.feed(resp.text)
    return parser.results[:max_results]


def _brave(query: str, max_results: int, api_key: str) -> list[dict[str, str]]:
    with httpx.Client(timeout=20.0, headers={"Accept": "application/json", "X-Subscription-Token": api_key}) as client:
        resp = client.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
        )
        resp.raise_for_status()
        data = resp.json()
    out = []
    for item in (data.get("web") or {}).get("results") or []:
        out.append(
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "snippet": item.get("description") or "",
            }
        )
    return out


def search_web(query: str, max_results: int = 8) -> list[dict[str, str]]:
    q = (query or "").strip()
    if not q:
        return []
    provider = (get_setting("search_provider") or "auto").lower()
    brave_key = get_setting("search_api_key") or ""
    errors: list[str] = []
    if provider in {"brave", "auto"} and brave_key:
        try:
            return _brave(q, max_results, str(brave_key))
        except Exception as exc:
            errors.append(f"brave: {exc}")
            if provider == "brave":
                LOG.warning("brave search failed: %s", exc)
                return []
    try:
        return _ddg_library(q, max_results)
    except Exception as exc:
        errors.append(str(exc))
    try:
        return _ddg_html(q, max_results)
    except Exception as exc:
        LOG.warning("web search failed for %r: %s / %s", q[:80], errors, exc)
        return []


def search_standards(query: str, intent: str = "standards", max_results: int = 8) -> dict[str, Any]:
    rewritten = rewrite_query(query, intent=intent)
    raw = search_web(rewritten, max_results=max_results)
    annotated = [annotate_result(item) for item in raw]
    if intent == "standards" and not any(item.get("source") == "body" for item in annotated):
        extra = search_web(rewritten + SITE_BOOST, max_results=max(4, max_results // 2))
        annotated.extend(annotate_result(item) for item in extra)
    ranked = rank_results(dedupe_results(annotated))
    if intent == "parts" and not any(item.get("source") == "vendor" for item in ranked):
        extra = search_web(rewritten + PARTS_SITE_BOOST, max_results=max(4, max_results // 2))
        annotated.extend(annotate_result(item) for item in extra)
        ranked = rank_results(dedupe_results(annotated))
    note = (
        "Prefer standards bodies, then manufacturer datasheets. Full standard "
        "PDFs are often paywalled — cite the document and the publicly stated "
        "requirement; do not invent clauses."
    )
    if intent == "parts":
        note = (
            "Hobby-vendor search hits, not live inventory. Do not claim a part "
            "is in stock or at a price because a snippet said so. Confirm on "
            "the vendor page. Bundled catalog prices are street-typical."
        )
    return {
        "ok": True,
        "query": query,
        "rewritten": rewritten,
        "intent": intent,
        "results": ranked[:max_results],
        "citations": [
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "source": item.get("source"),
                "standard_ids": item.get("standard_ids") or [],
            }
            for item in ranked[:max_results]
        ],
        "note": note,
    }


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _STRIP:
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in _STRIP and self._skip:
            self._skip -= 1
        if tag in {"p", "div", "li", "h1", "h2", "h3", "tr", "br"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        text = data.strip()
        if text:
            self.parts.append(text)


_PRIVATE = re.compile(
    r"^(127\.|10\.|192\.168\.|169\.254\.|0\.|::1|localhost|172\.(1[6-9]|2\d|3[01])\.)",
    re.I,
)


def _blocked_url(url: str) -> str | None:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "only http(s) URLs can be fetched"
    host = (parsed.hostname or "").lower()
    if _PRIVATE.match(host) or host in {"localhost"}:
        return "refusing to fetch a private/loopback address"
    return None


def read_url(url: str, max_chars: int = 8000) -> dict[str, Any]:
    reason = _blocked_url(url)
    if reason:
        return {"ok": False, "url": url, "error": reason}
    try:
        with httpx.Client(timeout=20.0, headers=HEADERS, follow_redirects=False) as client:
            current = url
            for _ in range(5):
                blocked = _blocked_url(current)
                if blocked:
                    return {"ok": False, "url": current, "error": blocked}
                resp = client.get(current)
                if resp.is_redirect:
                    nxt = resp.headers.get("location") or ""
                    current = urljoin(current, nxt)
                    continue
                break
            else:
                return {"ok": False, "url": url, "error": "too many redirects"}
            if resp.status_code >= 400:
                return {
                    "ok": False,
                    "url": str(resp.url),
                    "error": f"HTTP {resp.status_code}",
                    "paywalled": resp.status_code in {401, 403, 402},
                }
            ctype = (resp.headers.get("content-type") or "").lower()
            if "pdf" in ctype:
                return {
                    "ok": False,
                    "url": str(resp.url),
                    "error": "PDF body not extracted. This is often a paid standard. Cite the URL, do not guess clauses.",
                    "paywalled": True,
                    "standard_ids": extract_standard_ids(str(resp.url)),
                }
            raw = resp.content[: 2 * 1024 * 1024].decode(resp.encoding or "utf-8", errors="replace")
    except Exception as exc:
        return {"ok": False, "url": url, "error": str(exc)}

    extractor = _TextExtractor()
    try:
        extractor.feed(raw)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", raw)
    else:
        text = re.sub(r"\n{3,}", "\n\n", " ".join(extractor.parts))
    text = re.sub(r"[ \t]+", " ", text).strip()
    paywalled = bool(
        re.search(r"subscribe|purchase the standard|login to (download|view)|add to cart", text, re.I)
    )
    return {
        "ok": True,
        "url": str(resp.url),
        "title": "",
        "text": text[:max_chars],
        "standard_ids": extract_standard_ids(text + " " + url),
        "paywalled": paywalled,
        "official": is_official(str(resp.url)),
    }
