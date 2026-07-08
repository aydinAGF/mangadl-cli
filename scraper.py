import re
import os
import urllib.request
import urllib.parse
from typing import List, Optional, Tuple, Dict

BASE_URL = "https://weebcentral.com"
REQUEST_TIMEOUT = 30 if os.path.isdir("/data/data/com.termux") or "TERMUX_VERSION" in os.environ else 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Termux) AppleWebKit/537.36",
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
    try:
        html = _post(
            f"{BASE_URL}/search/simple?location=main",
            {"text": query},
            {"HX-Request": "true"},
        )
    except Exception:
        return []

    results = []
    pairs = re.findall(
        r'href="(https://weebcentral\.com/series/([^/]+)/([^"]+))"[^>]*>.*?'
        r'<div[^>]*class="[^"]*flex-1[^"]*"[^>]*>\s*(.*?)\s*</div>',
        html,
        re.DOTALL,
    )
    for url, series_id, slug, raw_title in pairs:
        title = re.sub(r"<[^>]+>", "", raw_title).strip()
        if title:
            results.append({"title": title, "url": url, "series_id": series_id, "slug": slug})
    return results


def get_all_chapters(series_id: str) -> List[Tuple[float, str]]:
    url = f"{BASE_URL}/series/{series_id}/full-chapter-list"
    try:
        html = _get(url, {"HX-Request": "true"})
    except Exception:
        return []

    pairs = re.findall(
        r'href="(https://weebcentral\.com/chapters/[^"]+)"[^>]*>.*?'
        r'<span class="">\s*([^<]+?)\s*</span>',
        html,
        re.DOTALL,
    )

    result = []
    for ch_url, title_text in pairs:
        num_match = re.search(r'([\d]+(?:\.\d+)?)\s*$', title_text.strip())
        if not num_match:
            continue
        try:
            ch_num = float(num_match.group(1))
            result.append((ch_num, ch_url))
        except ValueError:
            continue

    result.sort(key=lambda x: x[0])
    return result


def filter_chapters(
    chapters: List[Tuple[float, str]], chapter_range: str
) -> List[Tuple[float, str]]:
    if chapter_range.strip().lower() == "all":
        return chapters

    ch_map = {ch_num: ch_url for ch_num, ch_url in chapters}
    all_nums = sorted(ch_map.keys())

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
                closest = min(all_nums, key=lambda x: abs(x - n))
                if abs(closest - n) < 0.6:
                    selected.add(closest)
            except ValueError:
                pass

    return [(n, ch_map[n]) for n in sorted(selected) if n in ch_map]


def get_chapter_images(chapter_url: str) -> List[str]:
    chapter_id = chapter_url.rstrip("/").split("/")[-1]
    img_url = f"{BASE_URL}/chapters/{chapter_id}/images?is_prev=False&reading_style=long_strip"
    try:
        html = _get(img_url, {"HX-Request": "true"})
    except Exception:
        return []

    imgs = re.findall(
        r'<img\s[^>]*src="(https://[^"]+\.(?:jpg|png|webp|jpeg)[^"]*)"',
        html,
        re.IGNORECASE,
    )
    seen = set()
    unique = []
    for img in imgs:
        if img not in seen:
            seen.add(img)
            unique.append(img)
    return unique
