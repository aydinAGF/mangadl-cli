import sys
import os
import re
import json
import time
import threading
import shutil
import argparse
from typing import List, Optional

from .scraper import search_manga, get_all_chapters, filter_chapters, get_chapter_images
from .downloader import download_chapter, download_chapter_temp, _pack_cbz_merged, _is_termux, DEFAULT_DOWNLOAD_DIR, _has_aria2c, _has_pillow

BANNER = r"""
  ╔══════════════════════════════════════════════╗
  ║               Manga Downloader               ║
  ║  weebcentral.com  |  aydinAGF  |   CBZ/PDF   ║
  ╚══════════════════════════════════════════════╝
"""

HELP = """
  Chapter range formats:
    1          → single chapter
    2-8        → chapters 2 to 8
    1-90       → chapters 1 to 90
    all        → every chapter
    1,5,10     → specific chapters
    1-10,20    → mixed
"""

FORMAT_MENU = """
  Output format:
    1. CBZ           — comic book archive (one per chapter)
    2. PDF           — single PDF per chapter{pdf_note}
    3. Images        — raw image folder (one folder per chapter)
    4. CBZ (All-in-One) — merge all selected chapters into one big CBZ
"""


def _session_path(download_dir: str, safe_title: str) -> str:
    return os.path.join(download_dir, safe_title, ".mangadl_session.json")


def _load_session(download_dir: str, safe_title: str) -> dict:
    path = _session_path(download_dir, safe_title)
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_session(download_dir: str, safe_title: str, session: dict):
    path = _session_path(download_dir, safe_title)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(session, f, indent=2)


def _clear_session(download_dir: str, safe_title: str):
    path = _session_path(download_dir, safe_title)
    if os.path.exists(path):
        os.remove(path)


def _live_search() -> Optional[dict]:
    try:
        from prompt_toolkit import prompt
        from prompt_toolkit.completion import Completer, Completion

        class MangaCompleter(Completer):
            def __init__(self):
                self._cache: dict = {}
                self._last_text = ""
                self._results: List[dict] = []

            def get_completions(self, document, complete_event):
                text = document.text_before_cursor.strip()
                if len(text) < 2 or text == self._last_text:
                    for r in self._results:
                        yield Completion(r["title"], start_position=-len(text))
                    return
                self._last_text = text
                try:
                    self._results = search_manga(text)
                except Exception:
                    self._results = []
                for r in self._results:
                    yield Completion(r["title"], start_position=-len(text))

        completer = MangaCompleter()
        print("\n  Type manga name — suggestions appear as you type. Press Tab to pick, Enter to search.\n")
        try:
            query = prompt("  Search: ", completer=completer, complete_while_typing=True)
        except (KeyboardInterrupt, EOFError):
            return None

        query = query.strip()
        if not query:
            return None
        print(f"\n  Searching for '{query}'...")
        results = search_manga(query)

    except ImportError:
        try:
            print("\n  Type manga name and press Enter.\n")
            query = input("  Search: ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not query:
            return None
        print(f"  Searching for '{query}'...")
        results = search_manga(query)

    if not results:
        print("  [!] No results found. Try a different name.")
        return None

    print()
    print("  +--------------------------------------------+")
    print("  |              Search Results                 |")
    print("  +--------------------------------------------+")
    for i, r in enumerate(results[:15], 1):
        title = r["title"][:48]
        print(f"  |  {i:>2}. {title:<48}|")
    print("  +--------------------------------------------+")

    while True:
        try:
            choice = input(f"\n  Pick a number (1-{min(len(results), 15)}): ").strip()
            if not choice:
                continue
            idx = int(choice) - 1
            if 0 <= idx < min(len(results), 15):
                return results[idx]
            print("  [!] Invalid choice.")
        except ValueError:
            print("  [!] Please enter a number.")
        except (KeyboardInterrupt, EOFError):
            return None


def _get_chapter_range(ch_min: float, ch_max: float, total: int) -> str:
    print(HELP)
    while True:
        try:
            raw = input(f"  Enter chapter range ({ch_min:.0f}-{ch_max:.0f} available): ").strip()
            if raw:
                return raw
        except (KeyboardInterrupt, EOFError):
            return ""


def _get_output_format() -> str:
    pdf_note = "" if _has_pillow() else "  [!] requires: pip install Pillow"
    print(FORMAT_MENU.format(pdf_note=pdf_note))
    formats = {1: "cbz", 2: "pdf", 3: "images", 4: "cbz-merge"}
    while True:
        try:
            raw = input("  Select format (1-4) [default: 1]: ").strip()
            if not raw:
                return "cbz"
            num = int(raw)
            if num in formats:
                chosen = formats[num]
                if chosen == "pdf" and not _has_pillow():
                    print("  [!] Pillow not installed. Install it first: pip install Pillow")
                    continue
                return chosen
            print("  [!] Enter 1, 2, 3, or 4.")
        except ValueError:
            print("  [!] Enter 1, 2, 3, or 4.")
        except (KeyboardInterrupt, EOFError):
            return "cbz"


def _progress_bar(done: int, total: int, width: int = 30) -> str:
    pct = done / total if total else 0
    filled = int(width * pct)
    bar = "#" * filled + "-" * (width - filled)
    return f"[{bar}] {done}/{total}"


def _spinner_thread(stop: threading.Event, label: str):
    frames = ["|", "/", "-", "\\"]
    i = 0
    while not stop.is_set():
        sys.stdout.write(f"\r  {frames[i % len(frames)]}  {label}...")
        sys.stdout.flush()
        stop.wait(0.1)
        i += 1
    sys.stdout.write("\r" + " " * 70 + "\r")
    sys.stdout.flush()


def _pick_download_dir(default: str) -> str:
    print(f"  Download directory: {default}")
    try:
        raw = input("  Change directory? (y/N) [default: keep current]: ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if raw not in ("y", "yes"):
        return default
    print("  Enter new path (or press Enter to keep current):")
    try:
        new_path = input(f"  [{default}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if not new_path:
        return default
    new_path = os.path.expanduser(new_path)
    os.makedirs(new_path, exist_ok=True)
    print(f"  Download directory set to: {new_path}\n")
    return new_path


def main():
    parser = argparse.ArgumentParser(
        prog="mangadl",
        description="Manga downloader for Termux — downloads from weebcentral.com",
        add_help=True,
    )
    parser.add_argument(
        "--dir",
        metavar="PATH",
        default=None,
        help=f"Set download directory (default: {DEFAULT_DOWNLOAD_DIR})",
    )
    args = parser.parse_args()

    print(BANNER)

    if not _has_aria2c():
        if _is_termux():
            print("  [~] aria2c not found — downloads will use threads (slower).")
            print("      Install with: pkg install aria2\n")
        else:
            print("  [~] aria2c not found — downloads will use threads (slower).")
            print("      Install: https://aria2.github.io/\n")

    if args.dir:
        download_dir = os.path.expanduser(args.dir)
        os.makedirs(download_dir, exist_ok=True)
        print(f"  Download directory: {download_dir}\n")
    else:
        download_dir = os.environ.get("MANGA_DOWNLOAD_DIR", DEFAULT_DOWNLOAD_DIR)
        download_dir = _pick_download_dir(download_dir)

    while True:
        manga = _live_search()
        if not manga:
            print("\n  Goodbye!\n")
            break

        title = manga["title"]
        series_id = manga["series_id"]
        print(f"\n  Selected: {title}")

        stop = threading.Event()
        t = threading.Thread(target=_spinner_thread, args=(stop, "Fetching chapter list"), daemon=True)
        t.start()
        chapters = get_all_chapters(series_id)
        stop.set()
        t.join()

        if not chapters:
            print("  [!] Could not fetch chapter list. Try again.")
            continue

        total_ch = len(chapters)
        ch_min = chapters[0][0]
        ch_max = chapters[-1][0]
        print(f"  Found {total_ch} chapters  (Ch {ch_min:.0f} - Ch {ch_max:.0f})\n")

        raw_range = _get_chapter_range(ch_min, ch_max, total_ch)
        if not raw_range:
            continue

        selected = filter_chapters(chapters, raw_range)
        if not selected:
            print("  [!] No chapters matched that range.")
            continue

        output_format = _get_output_format()
        ext_label = {"cbz": "CBZ", "pdf": "PDF", "images": "Images", "cbz-merge": "CBZ (All-in-One)"}[output_format]

        print(f"\n  Downloading {len(selected)} chapter(s) -> {ext_label}")
        print(f"  Saving to: {download_dir}/{title}/")
        print(f"  Downloader: {'aria2c' if _has_aria2c() else 'threaded'}\n")

        safe_title = re.sub(r'[\\/*?:"<>|]', "_", title).strip()

        if output_format == "cbz-merge":
            session = _load_session(download_dir, safe_title)
            resume = False
            tmp_dirs = []
            downloaded_chapters = set()

            if session and session.get("title") == title:
                saved_selection = session.get("selected_chapters", [])
                saved_tmps = session.get("tmp_dirs", [])
                valid_tmps = [td for td in saved_tmps if os.path.isdir(td)]
                if valid_tmps and saved_selection:
                    downloaded_chapters = set(saved_selection)
                    tmp_dirs = valid_tmps
                    print(f"  [~] Found previous session: {len(valid_tmps)} chapter(s) already downloaded.")
                    try:
                        choice = input("  Resume? (Y/n): ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        choice = "n"
                    if choice != "n":
                        resume = True
                        print(f"  Resuming with {len(valid_tmps)} cached chapter(s)...\n")

            if not resume:
                session = {"title": title, "selected_chapters": [], "tmp_dirs": []}
                downloaded_chapters = set()

            pending = [(ch_num, ch_url) for ch_num, ch_url in selected if ch_num not in downloaded_chapters]
            total_selected = len(selected)

            max_retries = 3
            for attempt in range(max_retries):
                if attempt > 0:
                    print(f"\n  Retry attempt {attempt + 1}/{max_retries}...")
                    pending = [(ch_num, ch_url) for ch_num, ch_url in selected if ch_num not in downloaded_chapters]

                if not pending:
                    break

                for i, (ch_num, ch_url) in enumerate(pending, 1):
                    idx = [c for c, _ in selected].index(ch_num) + 1
                    ch_label = f"Ch {ch_num:.0f}" if ch_num == int(ch_num) else f"Ch {ch_num}"
                    prefix = f"  [{idx}/{total_selected}]  {ch_label}"

                    stop2 = threading.Event()
                    t2 = threading.Thread(target=_spinner_thread, args=(stop2, f"{prefix} -- fetching pages"), daemon=True)
                    t2.start()
                    images = get_chapter_images(ch_url)
                    stop2.set()
                    t2.join()

                    if not images:
                        print(f"  [!] {ch_label}: no images found, skipping.")
                        continue

                    page_count = len(images)

                    def on_progress(done: int, total: int, _prefix=prefix):
                        bar = _progress_bar(done, total)
                        sys.stdout.write(f"\r  {_prefix}  {bar}")
                        sys.stdout.flush()

                    sys.stdout.write(f"\r  {prefix}  {_progress_bar(0, page_count)}")
                    sys.stdout.flush()

                    tmp_dir, ok, log = download_chapter_temp(ch_num, images, on_progress)

                    if ok:
                        tmp_dirs.append(tmp_dir)
                        downloaded_chapters.add(ch_num)
                        sys.stdout.write(f"\r  {prefix}  {_progress_bar(page_count, page_count)}  OK\n")
                        session["selected_chapters"] = list(downloaded_chapters)
                        session["tmp_dirs"] = tmp_dirs
                        _save_session(download_dir, safe_title, session)
                    else:
                        sys.stdout.write(f"\r  {prefix}  FAILED\n")
                        sys.stdout.flush()
                    sys.stdout.flush()

            failed = [ch for ch, _ in selected if ch not in downloaded_chapters]
            if failed:
                failed_labels = [f"Ch {ch:.0f}" if ch == int(ch) else f"Ch {ch}" for ch in failed]
                print(f"\n  [!] {len(failed)} chapter(s) failed: {', '.join(failed_labels)}")
                while True:
                    try:
                        choice = input("  Retry failed chapters? (Y/n): ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        choice = "n"
                    if choice == "n":
                        break
                    retry_failed = [(ch, [u for c, u in selected if c == ch][0]) for ch in failed]
                    for ch_num, ch_url in retry_failed:
                        idx = [c for c, _ in selected].index(ch_num) + 1
                        ch_label = f"Ch {ch_num:.0f}" if ch_num == int(ch_num) else f"Ch {ch_num}"
                        prefix = f"  [{idx}/{total_selected}]  {ch_label}"

                        stop2 = threading.Event()
                        t2 = threading.Thread(target=_spinner_thread, args=(stop2, f"{prefix} -- fetching pages"), daemon=True)
                        t2.start()
                        images = get_chapter_images(ch_url)
                        stop2.set()
                        t2.join()

                        if not images:
                            print(f"  [!] {ch_label}: no images found, skipping.")
                            continue

                        page_count = len(images)

                        def on_progress(done: int, total: int, _prefix=prefix):
                            bar = _progress_bar(done, total)
                            sys.stdout.write(f"\r  {_prefix}  {bar}")
                            sys.stdout.flush()

                        sys.stdout.write(f"\r  {prefix}  {_progress_bar(0, page_count)}")
                        sys.stdout.flush()

                        tmp_dir, ok, log = download_chapter_temp(ch_num, images, on_progress)

                        if ok:
                            tmp_dirs.append(tmp_dir)
                            downloaded_chapters.add(ch_num)
                            sys.stdout.write(f"\r  {prefix}  {_progress_bar(page_count, page_count)}  OK\n")
                            session["selected_chapters"] = list(downloaded_chapters)
                            session["tmp_dirs"] = tmp_dirs
                            _save_session(download_dir, safe_title, session)
                        else:
                            sys.stdout.write(f"\r  {prefix}  FAILED\n")
                            sys.stdout.flush()
                        sys.stdout.flush()
                    failed = [ch for ch, _ in selected if ch not in downloaded_chapters]
                    if not failed:
                        break
                    print(f"\n  [!] Still {len(failed)} chapter(s) failed.")

            if tmp_dirs:
                out_path = os.path.join(download_dir, safe_title, f"{safe_title} - Merged ({len(tmp_dirs)} ch).cbz")
                os.makedirs(os.path.dirname(out_path), exist_ok=True)

                def on_merge_progress(done: int, total: int):
                    bar = _progress_bar(done, total)
                    sys.stdout.write(f"\r  Merging pages  {bar}")
                    sys.stdout.flush()

                print(f"\n  Merging {len(tmp_dirs)} chapter(s) into one CBZ...")
                merge_ok, merge_log = _pack_cbz_merged(tmp_dirs, out_path, on_merge_progress)

                if merge_ok:
                    for td in tmp_dirs:
                        shutil.rmtree(td, ignore_errors=True)
                    _clear_session(download_dir, safe_title)
                    sys.stdout.write(f"\r  Merging pages  {_progress_bar(1, 1)}  OK\n")
                    print(f"\n  Saved: {out_path}")
                    print(f"    ({len(downloaded_chapters)}/{total_selected} chapters, {merge_log})")
                    if failed:
                        print(f"    [!] {len(failed)} chapter(s) skipped: {', '.join(f'Ch {c:.0f}' if c == int(c) else f'Ch {c}' for c in failed)}")
                else:
                    print(f"\n  [!] Merge failed: {merge_log}")
                    print("  Temp files kept — you can resume next time.")

            continue

        ok_count = 0
        for i, (ch_num, ch_url) in enumerate(selected, 1):
            ch_label = f"Ch {ch_num:.0f}" if ch_num == int(ch_num) else f"Ch {ch_num}"
            prefix = f"  [{i}/{len(selected)}]  {ch_label}"

            stop2 = threading.Event()
            t2 = threading.Thread(target=_spinner_thread, args=(stop2, f"{prefix} -- fetching pages"), daemon=True)
            t2.start()
            images = get_chapter_images(ch_url)
            stop2.set()
            t2.join()

            if not images:
                print(f"  [!] {ch_label}: no images found, skipping.")
                continue

            page_count = len(images)

            def on_progress(done: int, total: int, _prefix=prefix):
                bar = _progress_bar(done, total)
                sys.stdout.write(f"\r  {_prefix}  {bar}")
                sys.stdout.flush()

            sys.stdout.write(f"\r  {prefix}  {_progress_bar(0, page_count)}")
            sys.stdout.flush()

            ok, log = download_chapter(
                ch_num, images, title, download_dir,
                on_progress, verbose=True,
                output_format=output_format,
            )

            ch_full = ch_label.replace("Ch ", "Chapter ")
            if output_format == "pdf":
                out_name = f"{safe_title} - {ch_full}.pdf"
            elif output_format == "images":
                out_name = f"{ch_full}/"
            else:
                out_name = f"{safe_title} - {ch_full}.cbz"

            if ok:
                sys.stdout.write(
                    f"\r  {prefix}  {_progress_bar(page_count, page_count)}  OK  ->  {out_name}\n"
                )
                ok_count += 1
            else:
                sys.stdout.write(f"\r  {prefix}  FAILED\n")
                sys.stdout.flush()
                print()
                print("  -- Error log -----------------------------------------")
                for line in log.strip().splitlines():
                    print(f"  {line}")
                print("  -------------------------------------------------------")
                print()
            sys.stdout.flush()

        print()
        if ok_count == len(selected):
            print(f"  All {ok_count} chapter(s) saved as {ext_label} to: {download_dir}/{title}/")
        else:
            print(f"  {ok_count}/{len(selected)} chapter(s) saved.")

        print()
        try:
            again = input("  Download another manga? (Y/n): ").strip().lower()
            if again == "n":
                print("\n  Goodbye!\n")
                break
        except (KeyboardInterrupt, EOFError):
            print("\n\n  Goodbye!\n")
            break


if __name__ == "__main__":
    main()
