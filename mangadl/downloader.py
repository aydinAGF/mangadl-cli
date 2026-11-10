import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
import urllib.request
from typing import List, Callable, Optional


def _is_termux() -> bool:
    return os.path.isdir("/data/data/com.termux") or "TERMUX_VERSION" in os.environ


def _get_temp_dir() -> str:
    if _is_termux():
        termux_tmp = "/data/data/com.termux/files/usr/tmp"
        if os.path.isdir(termux_tmp):
            return termux_tmp
    return tempfile.gettempdir()


DEFAULT_DOWNLOAD_DIR = "."
ARIA2C_CONNECTIONS = 8 if _is_termux() else 16
FALLBACK_WORKERS = 4 if _is_termux() else 8
MIN_IMAGE_BYTES = 2048
REQUEST_TIMEOUT = 30 if _is_termux() else 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://weebcentral.com/",
    "Accept": "image/png,image/webp,image/*,*/*",
}


def _safe_dir_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def _find_aria2c() -> Optional[str]:
    """Locate the aria2c executable (bundled aria2c package, PATH, or venv Scripts)."""
    try:
        from aria2c import ARIA2C
        if ARIA2C.is_file():
            return str(ARIA2C)
    except Exception:
        pass
    found = shutil.which("aria2c")
    if found:
        return found
    exe_name = "aria2c.exe" if os.name == "nt" else "aria2c"
    candidate = os.path.join(os.path.dirname(sys.executable), exe_name)
    if os.path.isfile(candidate):
        return candidate
    return None


def _has_aria2c() -> bool:
    return _find_aria2c() is not None


def _has_pillow() -> bool:
    try:
        import PIL  # noqa: F401
        return True
    except ImportError:
        return False


def _count_valid_images(directory: str) -> int:
    count = 0
    for f in os.listdir(directory):
        if f.startswith("."):
            continue
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
            path = os.path.join(directory, f)
            if os.path.getsize(path) >= MIN_IMAGE_BYTES:
                count += 1
    return count


def _sorted_images(directory: str) -> List[str]:
    files = []
    for f in sorted(os.listdir(directory)):
        if f.startswith("."):
            continue
        if f.lower().endswith((".png", ".jpg", ".jpeg", ".webp", ".gif")):
            path = os.path.join(directory, f)
            if os.path.getsize(path) >= MIN_IMAGE_BYTES:
                files.append(path)
    return files


def _clean_partial_files(directory: str) -> None:
    """Remove undersized files and aria2 control files so retries start clean."""
    for f in os.listdir(directory):
        fp = os.path.join(directory, f)
        if not os.path.isfile(fp):
            continue
        if f.endswith(".aria2") or os.path.getsize(fp) < MIN_IMAGE_BYTES:
            try:
                os.remove(fp)
            except OSError:
                pass


def _download_with_aria2c(urls: List[str], output_dir: str) -> tuple:
    aria2c_bin = _find_aria2c() or "aria2c"
    url_file = os.path.join(output_dir, ".aria2c_urls.txt")
    with open(url_file, "w", encoding="utf-8") as f:
        for i, url in enumerate(urls):
            ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
            filename = f"{i+1:03d}.{ext}"
            f.write(f"{url}\n")
            f.write(f"\tout={filename}\n")

    try:
        proc = subprocess.Popen(
            [
                aria2c_bin,
                f"--input-file={url_file}",
                f"--dir={output_dir}",
                f"-x{ARIA2C_CONNECTIONS}",
                f"-s{ARIA2C_CONNECTIONS}",
                "-k", "1M",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=0",
                "--console-log-level=notice",
                "--referer=https://weebcentral.com/",
                "--user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                "--header=Accept: image/png,image/webp,image/*,*/*",
                "--header=Accept-Language: en-US,en;q=0.9",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        raw_out, _ = proc.communicate()
        log = raw_out.decode("utf-8", errors="ignore")
        valid = _count_valid_images(output_dir)
        success = valid >= max(1, int(len(urls) * 0.9))
        return success, log
    finally:
        if os.path.exists(url_file):
            os.remove(url_file)


def _download_fallback(
    urls: List[str],
    output_dir: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    lock = threading.Lock()
    results = [False] * len(urls)
    errors: List[str] = []

    def worker(idx: int, url: str):
        ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
        path = os.path.join(output_dir, f"{idx+1:03d}.{ext}")
        ok = False
        try:
            req = urllib.request.Request(url, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                data = resp.read()
            if len(data) >= MIN_IMAGE_BYTES:
                with open(path, "wb") as fh:
                    fh.write(data)
                ok = True
        except Exception as e:
            with lock:
                errors.append(f"  [{idx+1:03d}] {e}")
        results[idx] = ok
        if on_progress:
            with lock:
                on_progress(sum(1 for r in results if r), len(urls))

    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=FALLBACK_WORKERS) as pool:
        futures = [pool.submit(worker, i, u) for i, u in enumerate(urls)]
        for f in futures:
            f.result()

    success = sum(results) >= max(1, int(len(urls) * 0.9))
    return success, "\n".join(errors) if errors else "OK"


def _download_images_to_dir(
    images: List[str],
    output_dir: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    """Download image list into output_dir using aria2c with threaded fallback."""
    log = ""
    if _has_aria2c():
        ok, log = _download_with_aria2c(images, output_dir)
        if on_progress:
            on_progress(len(images), len(images))
        if not ok:
            log += "\n  [~] aria2c failed — retrying with built-in downloader...\n"
            _clean_partial_files(output_dir)
            ok2, log2 = _download_fallback(images, output_dir, on_progress)
            log += log2
            ok = ok2
    else:
        ok, log = _download_fallback(images, output_dir, on_progress)

    if not ok:
        valid = _count_valid_images(output_dir)
        log += f"\n  [!] Only {valid}/{len(images)} pages downloaded successfully."
    return ok, log


def _pack_cbz(image_dir: str, cbz_path: str) -> bool:
    files = _sorted_images(image_dir)
    if not files:
        return False
    try:
        with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for fpath in files:
                zf.write(fpath, arcname=os.path.basename(fpath))
        return True
    except Exception:
        return False


def _collect_chapter_images(chapter_dirs: List[str]) -> List[str]:
    all_files: List[str] = []
    for ch_dir in chapter_dirs:
        if os.path.isdir(ch_dir):
            all_files.extend(_sorted_images(ch_dir))
    return all_files


def _pack_cbz_merged(
    chapter_dirs: List[str],
    cbz_path: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    """Merge sorted images across multiple chapter directories into one CBZ."""
    all_files = _collect_chapter_images(chapter_dirs)
    if not all_files:
        return False, "No valid images found across any chapter."

    total = len(all_files)
    pad = max(4, len(str(total)))
    try:
        with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for i, fpath in enumerate(all_files):
                ext = os.path.splitext(fpath)[1].lower() or ".jpg"
                arcname = f"{i + 1:0{pad}d}{ext}"
                zf.write(fpath, arcname=arcname)
                if on_progress:
                    on_progress(i + 1, total)
        return True, f"Merged {total} pages into single CBZ."
    except Exception as e:
        if os.path.exists(cbz_path):
            try:
                os.remove(cbz_path)
            except OSError:
                pass
        return False, f"Merged CBZ failed: {e}"


def _pack_pdf_merged(
    chapter_dirs: List[str],
    pdf_path: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    """Merge sorted images across multiple chapter directories into one PDF."""
    try:
        from PIL import Image
    except ImportError:
        return False, "PDF output requires Pillow: pip install Pillow"

    all_files = _collect_chapter_images(chapter_dirs)
    if not all_files:
        return False, "No valid images found across any chapter."

    total = len(all_files)
    chunk_size = 30
    try:
        for start in range(0, total, chunk_size):
            batch_paths = all_files[start:start + chunk_size]
            imgs = []
            try:
                for idx, fpath in enumerate(batch_paths):
                    with Image.open(fpath) as im:
                        imgs.append(im.convert("RGB"))
                    if on_progress:
                        on_progress(start + idx + 1, total)
                imgs[0].save(
                    pdf_path,
                    format="PDF",
                    save_all=True,
                    append_images=imgs[1:],
                    append=(start > 0),
                )
            finally:
                for im in imgs:
                    im.close()
        return True, f"Merged {total} pages into single PDF."
    except Exception as e:
        if os.path.exists(pdf_path):
            try:
                os.remove(pdf_path)
            except OSError:
                pass
        return False, f"Merged PDF failed: {e}"


def _pack_images_merged(
    chapter_dirs: List[str],
    dest_dir: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    """Gather sorted images across multiple chapter directories into one folder."""
    all_files = _collect_chapter_images(chapter_dirs)
    if not all_files:
        return False, "No valid images found across any chapter."

    total = len(all_files)
    pad = max(4, len(str(total)))
    try:
        os.makedirs(dest_dir, exist_ok=True)
        for i, fpath in enumerate(all_files):
            ext = os.path.splitext(fpath)[1].lower() or ".jpg"
            dest_file = os.path.join(dest_dir, f"{i + 1:0{pad}d}{ext}")
            shutil.copy2(fpath, dest_file)
            if on_progress:
                on_progress(i + 1, total)
        return True, f"Gathered {total} pages into folder."
    except Exception as e:
        return False, f"Merged images failed: {e}"


def download_chapter_temp(
    chapter_num: float,
    images: List[str],
    on_progress: Optional[Callable] = None,
    batch_id: Optional[str] = None,
) -> tuple:
    """Download a chapter's pages into a temporary directory for later merging."""
    if not images:
        return "", False, "No image URLs found for this chapter."

    batch_part = f"{_safe_dir_name(batch_id)}_" if batch_id else ""
    tmp_dir = os.path.join(_get_temp_dir(), f".mangadl_{batch_part}ch_{chapter_num}_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)
    _clean_partial_files(tmp_dir)

    try:
        ok, log = _download_images_to_dir(images, tmp_dir, on_progress)
        return tmp_dir, ok, log
    except Exception as e:
        return tmp_dir, False, str(e)


def _pack_pdf(image_dir: str, pdf_path: str) -> bool:
    try:
        from PIL import Image
    except ImportError:
        return False

    files = _sorted_images(image_dir)
    if not files:
        return False
    try:
        imgs = []
        for fpath in files:
            with Image.open(fpath) as im:
                imgs.append(im.convert("RGB"))
        if not imgs:
            return False
        imgs[0].save(
            pdf_path,
            save_all=True,
            append_images=imgs[1:],
            format="PDF",
        )
        return True
    except Exception:
        return False


def _copy_images(image_dir: str, dest_dir: str) -> bool:
    files = _sorted_images(image_dir)
    if not files:
        return False
    os.makedirs(dest_dir, exist_ok=True)
    for fpath in files:
        shutil.copy2(fpath, dest_dir)
    return True


def download_chapter(
    chapter_num: float,
    images: List[str],
    manga_title: str,
    base_dir: str = DEFAULT_DOWNLOAD_DIR,
    on_progress: Optional[Callable] = None,
    verbose: bool = False,
    output_format: str = "cbz",
) -> tuple:
    """
    Download all pages and save in the requested format.
    output_format: "cbz" | "pdf" | "images"
    Returns (success: bool, log: str)
    """
    if not images:
        return False, "No image URLs found for this chapter."

    base_dir = os.path.abspath(os.path.expanduser(base_dir))
    safe_title = _safe_dir_name(manga_title)
    ch_label = (
        f"Chapter {int(chapter_num)}"
        if chapter_num == int(chapter_num)
        else f"Chapter {chapter_num}"
    )

    series_dir = os.path.join(base_dir, safe_title)
    os.makedirs(series_dir, exist_ok=True)

    # Determine output path
    if output_format == "pdf":
        out_path = os.path.join(series_dir, f"{safe_title} - {ch_label}.pdf")
    elif output_format == "images":
        out_path = os.path.join(series_dir, ch_label)
    else:
        out_path = os.path.join(series_dir, f"{safe_title} - {ch_label}.cbz")

    # Skip if already done (for cbz/pdf check file, for images check dir)
    if output_format == "images":
        if os.path.isdir(out_path) and len(_sorted_images(out_path)) > 0:
            if on_progress:
                on_progress(len(images), len(images))
            return True, "Already downloaded (skipped)."
    else:
        if os.path.exists(out_path) and os.path.getsize(out_path) > 0:
            if on_progress:
                on_progress(len(images), len(images))
            return True, "Already downloaded (skipped)."

    tmp_dir = os.path.join(series_dir, f".tmp_{ch_label}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        ok, log = _download_images_to_dir(images, tmp_dir, on_progress)
        if not ok:
            return False, log

        # Pack / save
        if output_format == "pdf":
            packed = _pack_pdf(tmp_dir, out_path)
        elif output_format == "images":
            packed = _copy_images(tmp_dir, out_path)
        else:
            packed = _pack_cbz(tmp_dir, out_path)

        if not packed:
            if output_format == "pdf" and not _has_pillow():
                log += "\n  [!] PDF output requires Pillow: pip install Pillow"
            else:
                log += "\n  [!] Packing failed — no valid images in temp folder."
            return False, log

        return True, log

    finally:
        if os.path.exists(tmp_dir):
            shutil.rmtree(tmp_dir, ignore_errors=True)
