import os
import re
import shutil
import subprocess
import threading
import zipfile
import urllib.request
from typing import List, Callable, Optional


def _is_termux() -> bool:
    return os.path.isdir("/data/data/com.termux") or "TERMUX_VERSION" in os.environ


def _default_download_dir() -> str:
    if os.name == "nt":
        home = os.path.expanduser("~")
        for candidate in [
            os.path.join(home, "Downloads", "Manga"),
            os.path.join(home, "Manga"),
        ]:
            parent = os.path.dirname(candidate)
            if os.path.isdir(parent):
                return candidate
        return os.path.join(home, "Downloads", "Manga")
    if os.path.isdir("/sdcard"):
        return "/sdcard/Manga"
    home = os.path.expanduser("~")
    return os.path.join(home, "Downloads", "Manga")


def _get_temp_dir() -> str:
    if _is_termux():
        termux_tmp = "/data/data/com.termux/files/usr/tmp"
        if os.path.isdir(termux_tmp):
            return termux_tmp
        return "/tmp"
    for var in ["TEMP", "TMP"]:
        d = os.environ.get(var)
        if d and os.path.isdir(d):
            return d
    return "/tmp"


DEFAULT_DOWNLOAD_DIR = _default_download_dir()
ARIA2C_CONNECTIONS = 8 if _is_termux() else 16
FALLBACK_WORKERS = 4 if _is_termux() else 8
MIN_IMAGE_BYTES = 2048
REQUEST_TIMEOUT = 30 if _is_termux() else 20
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Linux; Android 13; Termux) AppleWebKit/537.36",
    "Referer": "https://weebcentral.com/",
    "Accept": "image/png,image/webp,image/*,*/*",
}


def _safe_dir_name(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name).strip()


def _has_aria2c() -> bool:
    return shutil.which("aria2c") is not None


def _has_pillow() -> bool:
    try:
        import PIL
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


def _download_with_aria2c(urls: List[str], output_dir: str) -> tuple:
    url_file = os.path.join(output_dir, ".aria2c_urls.txt")
    with open(url_file, "w") as f:
        for i, url in enumerate(urls):
            ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
            filename = f"{i+1:03d}.{ext}"
            f.write(f"{url}\n")
            f.write(f"\tout={filename}\n")

    try:
        proc = subprocess.Popen(
            [
                "aria2c",
                f"--input-file={url_file}",
                f"--dir={output_dir}",
                f"-x{ARIA2C_CONNECTIONS}",
                f"-s{ARIA2C_CONNECTIONS}",
                "-k", "512K",
                "--auto-file-renaming=false",
                "--allow-overwrite=true",
                "--summary-interval=0",
                "--console-log-level=notice",
                "--referer=https://weebcentral.com/",
                "--user-agent=Mozilla/5.0 (Linux; Android 13; Termux) AppleWebKit/537.36",
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


def _pack_cbz_merged(
    chapter_dirs: List[str],
    cbz_path: str,
    on_progress: Optional[Callable] = None,
) -> tuple:
    all_files = []
    for ch_dir in chapter_dirs:
        files = _sorted_images(ch_dir)
        all_files.extend(files)

    if not all_files:
        return False, "No valid images found across any chapter."

    try:
        total = len(all_files)
        with zipfile.ZipFile(cbz_path, "w", compression=zipfile.ZIP_STORED) as zf:
            for i, fpath in enumerate(all_files):
                ext = os.path.splitext(fpath)[1].lower() or ".jpg"
                arcname = f"{i + 1:04d}{ext}"
                zf.write(fpath, arcname=arcname)
                if on_progress:
                    on_progress(i + 1, total)
        return True, f"Merged {total} pages into single CBZ."
    except Exception as e:
        return False, f"Merged CBZ failed: {e}"


def download_chapter_temp(
    chapter_num: float,
    images: List[str],
    on_progress: Optional[Callable] = None,
) -> tuple:
    tmp_dir = os.path.join(_get_temp_dir(), f".mangadl_ch_{chapter_num}_{os.getpid()}")
    os.makedirs(tmp_dir, exist_ok=True)

    try:
        if _has_aria2c():
            ok, log = _download_with_aria2c(images, tmp_dir)
            if on_progress:
                on_progress(len(images), len(images))
            if not ok:
                log += "\n  [~] aria2c failed — retrying with built-in downloader...\n"
                for f in os.listdir(tmp_dir):
                    fp = os.path.join(tmp_dir, f)
                    if os.path.getsize(fp) < MIN_IMAGE_BYTES:
                        os.remove(fp)
                ok2, log2 = _download_fallback(images, tmp_dir, on_progress)
                log += log2
                ok = ok2
        else:
            ok, log = _download_fallback(images, tmp_dir, on_progress)

        if not ok:
            valid = _count_valid_images(tmp_dir)
            log += f"\n  [!] Only {valid}/{len(images)} pages downloaded successfully."
            return tmp_dir, False, log

        return tmp_dir, True, log
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
            img = Image.open(fpath).convert("RGB")
            imgs.append(img)
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
    if not images:
        return False, "No image URLs found for this chapter."

    safe_title = _safe_dir_name(manga_title)
    ch_label = (
        f"Chapter {int(chapter_num)}"
        if chapter_num == int(chapter_num)
        else f"Chapter {chapter_num}"
    )

    series_dir = os.path.join(base_dir, safe_title)
    os.makedirs(series_dir, exist_ok=True)

    if output_format == "pdf":
        out_path = os.path.join(series_dir, f"{safe_title} - {ch_label}.pdf")
    elif output_format == "images":
        out_path = os.path.join(series_dir, ch_label)
    else:
        out_path = os.path.join(series_dir, f"{safe_title} - {ch_label}.cbz")

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

    log = ""
    try:
        if _has_aria2c():
            ok, log = _download_with_aria2c(images, tmp_dir)
            if on_progress:
                on_progress(len(images), len(images))
            if not ok:
                log += "\n  [~] aria2c failed — retrying with built-in downloader...\n"
                for f in os.listdir(tmp_dir):
                    fp = os.path.join(tmp_dir, f)
                    if os.path.getsize(fp) < MIN_IMAGE_BYTES:
                        os.remove(fp)
                ok2, log2 = _download_fallback(images, tmp_dir, on_progress)
                log += log2
                ok = ok2
        else:
            ok, log = _download_fallback(images, tmp_dir, on_progress)

        if not ok:
            valid = _count_valid_images(tmp_dir)
            log += f"\n  [!] Only {valid}/{len(images)} pages downloaded successfully."
            return False, log

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
