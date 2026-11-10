import html as html_lib
import os
import re
import urllib.request
import urllib.parse
from typing import List, Optional, Tuple, Dict

BASE_URL = "https://weebcentral.com"
REQUEST_TIMEOUT = 30 if os.path.isdir("/data/data/com.termux") or "TERMUX_VERSION" in os.environ else 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,*/*",
    "Referer": BASE_URL + "/",
}


def _get(url: str, extra_headers: Optional[dict] = None) -> str:
    h = dict(HEADERS)
    if extra_headers:
        h.update(extra_headers)
    req = urllib.request.Request(url, headers=h)
    resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
    return resp.read().decode("utf-8", errors="ignore")


def _post(url: str, data: dict, extra_headers: Optional[dict] = None) -> str:
    h = dict(HEADERS)
    h["Content-Type"] = "application/x-www-form-urlencoded"
    h["HX-Request"] = "true"
    if extra_headers:
        h.update(extra_headers)
    encoded = urllib.parse.urlencode(data).encode()
    req = urllib.request.Request(url, data=encoded, headers=h)
    resp = urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT)
    return resp.read().decode("utf-8", errors="ignore")


def search_manga(query: str) -> List[Dict]:
    """
    POST to search/simple and parse results.
    Returns list of {title, url, series_id} dicts.
    """
    try:
        html = _post(
            f"{BASE_URL}/search/simple?location=main",
            {"text": query},
            {"HX-Request": "true"},
        )
    except Exception:
        return []

    results = []
    # Pattern: href="/series/{ID}/{slug}" or full URL, then title text
    pairs = re.findall(
        r'href="((?:https://weebcentral\.com)?/series/([^/]+)/([^"]+))"[^>]*>.*?'
        r'<div[^>]*class="[^"]*flex-1[^"]*"[^>]*>\s*(.*?)\s*</div>',
        html,
        re.DOTALL,
    )
    for url, series_id, slug, raw_title in pairs:
        if url.startswith("/"):
            url = BASE_URL + url
        title = html_lib.unescape(re.sub(r"<[^>]+>", "", raw_title)).strip()
        if title:
            results.append({"title": title, "url": url, "series_id": series_id, "slug": slug})
    return results


def get_all_chapters(series_id: str) -> List[Tuple[float, str]]:
    """
    Fetch full chapter list for a series.
    Returns list of (chapter_number, chapter_url) sorted ascending.
    """
    url = f"{BASE_URL}/series/{series_id}/full-chapter-list"
    try:
        html = _get(url, {"HX-Request": "true"})
    except Exception:
        return []

    # Extract URL + title span text for each chapter entry.
    # The span with class="" holds the chapter title, which may be:
    #   "Chapter 52"  →  extract trailing number 52
    #   "marriage 52" →  extract trailing number 52
    #   "EP 10"       →  extract trailing number 10
    pairs = re.findall(
        r'href="((?:https://weebcentral\.com)?/chapters/[^"]+)"[^>]*>.*?'
        r'<span class="">\s*([^<]+?)\s*</span>',
        html,
        re.DOTALL,
    )

    result = []
    for ch_url, title_text in pairs:
        if ch_url.startswith("/"):
            ch_url = BASE_URL + ch_url
        # Take the last number in the title as the chapter number
        num_match = re.search(r'([\d]+(?:\.\d+)?)\s*$', title_text.strip())
        if not num_match:
            continue
        try:
            ch_num = float(num_match.group(1))
            result.append((ch_num, ch_url))
        except ValueError:
            continue

    # Sort ascending by chapter number
    result.sort(key=lambda x: x[0])
    return result


def filter_chapters(
    chapters: List[Tuple[float, str]], chapter_range: str
) -> List[Tuple[float, str]]:
    """
    Filter chapters by range string.
    Supports: "all", "5", "2-8", "1,3,5", "1-10,20,30-35"
    """
    if chapter_range.strip().lower() == "all":
        return chapters

    ch_map = {ch_num: ch_url for ch_num, ch_url in chapters}
    all_nums = sorted(ch_map.keys())
    if not all_nums:
        return []

    selected = set()
    for part in chapter_range.split(","):
        part = part.strip()
        if "-" in part:
            bounds = part.split("-", 1)
            try:
                lo, hi = float(bounds[0]), float(bounds[1])
                for n in all_nums:
                    if lo <= n <= hi:
                        selected.add(n)
            except ValueError:
                pass
        else:
            try:
                n = float(part)
                # find closest
                closest = min(all_nums, key=lambda x: abs(x - n))
                if abs(closest - n) < 0.6:
                    selected.add(closest)
            except ValueError:
                pass

    return [(n, ch_map[n]) for n in sorted(selected) if n in ch_map]


def parse_batch_ranges(
    chapters: List[Tuple[float, str]], batch_spec: str
) -> List[Tuple[str, List[Tuple[float, str]]]]:
    """
    Split a '*' separated batch specification into (label, matched_chapters) pairs.
    Example: '1-10,15 * 24-30,32' -> [('1-10,15', [...]), ('24-30,32', [...])]
    """
    parts = [part.strip() for part in batch_spec.split("*") if part.strip()]
    return [(part, filter_chapters(chapters, part)) for part in parts]


def get_chapter_images(chapter_url: str) -> List[str]:
    """
    Fetch all image URLs for a chapter.
    Returns list of direct image URLs.
    """
    chapter_id = chapter_url.rstrip("/").split("/")[-1]
    img_url = f"{BASE_URL}/chapters/{chapter_id}/images?is_prev=False&reading_style=long_strip"
    try:
        html = _get(img_url, {"HX-Request": "true"})
    except Exception:
        return []

    imgs = re.findall(
        r'<img\s[^>]*src="(https?://[^"]+\.(?:jpg|png|webp|jpeg)[^"]*)"',
        html,
        re.IGNORECASE,
    )
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for img in imgs:
        img = html_lib.unescape(img)
        if img not in seen:
            seen.add(img)
            unique.append(img)
    return unique
