import sys
import os
import re
import json
import threading
import shutil
import argparse
from typing import Dict, List, Optional, Tuple

from .scraper import (
    search_manga,
    get_all_chapters,
    filter_chapters,
    parse_batch_ranges,
    get_chapter_images,
)
from .downloader import (
    download_chapter,
    download_chapter_temp,
    _pack_cbz_merged,
    _pack_pdf_merged,
    _pack_images_merged,
    _sorted_images,
    _safe_dir_name,
    _is_termux,
    _has_aria2c,
    _has_pillow,
)

BANNER = r"""
  ╔══════════════════════════════════════════════╗
  ║        Manga Downloader - by aydinAGF        ║
  ║  weebcentral.com  |  aria2c x16  |  CBZ/PDF  ║
  ╚══════════════════════════════════════════════╝
"""

HELP = """
  Range examples:
    1 | 2-8 | 1,5,10 | 1-10,20 | all
    1-10,15 * 24-30,32  -> separate volumes (split by *)
"""

FORMAT_MENU = "\n  Format:  1. CBZ   2. PDF{pdf_note}   3. Images"
MODE_MENU = "  Mode:    1. Per Chapter   2. All-in-One   3. By Volume (*)"


def _format_ch_label(ch_num: float) -> str:
    return f"Ch {ch_num:.0f}" if ch_num == int(ch_num) else f"Ch {ch_num}"


# ─── Session persistence ──────────────────────────────────────────────────────

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


# ─── Search ───────────────────────────────────────────────────────────────────

def _search_manga() -> Optional[dict]:
    while True:
        try:
            query = input("\n  Search manga (Enter to exit): ").strip()
        except (KeyboardInterrupt, EOFError):
            return None
        if not query:
            return None

        stop = threading.Event()
        t = threading.Thread(
            target=_spinner_thread,
            args=(stop, f"Searching for '{query}'"),
            daemon=True,
        )
        t.start()
        results = search_manga(query)
        stop.set()
        t.join()

        if not results:
            print("  [!] No results found. Try a different name.")
            continue

        print()
        print("  +----------------------------------------------------+")
        print("  |              Search Results                        |")
        print("  +----------------------------------------------------+")
        for i, r in enumerate(results[:15], 1):
            title = r["title"][:48]
            print(f"  |  {i:>2}. {title:<48}|")
        print("  +----------------------------------------------------+")

        while True:
            try:
                choice = input(f"\n  Pick a number (1-{min(len(results), 15)}, 0 to search again): ").strip()
                if not choice:
                    continue
                idx = int(choice) - 1
                if idx == -1:
                    break
                if 0 <= idx < min(len(results), 15):
                    return results[idx]
                print("  [!] Invalid choice.")
            except ValueError:
                print("  [!] Please enter a number.")
            except (KeyboardInterrupt, EOFError):
                return None


# ─── Chapter & format selection ───────────────────────────────────────────────

def _get_chapter_range(ch_min: float, ch_max: float) -> str:
    print(HELP)
    while True:
        try:
            raw = input(f"  Enter chapter range ({ch_min:.0f}-{ch_max:.0f} available): ").strip()
            if raw:
                return raw
        except (KeyboardInterrupt, EOFError):
            return ""


def _prompt_volume_batches(
    chapters: List[Tuple[float, str]],
    current_batches: List[Tuple[str, List[Tuple[float, str]]]],
) -> List[Tuple[str, List[Tuple[float, str]]]]:
    """Ask for '*'-separated volume ranges when By Volume is picked without '*'."""
    print("\n  Enter volume ranges separated by * (e.g. 1-10,15 * 24-30,32)")
    while True:
        try:
            raw = input("  Batches [Enter to keep current]: ").strip()
        except (KeyboardInterrupt, EOFError):
            return current_batches
        if not raw:
            return current_batches
        parsed = parse_batch_ranges(chapters, raw)
        valid = [(lbl, chs) for lbl, chs in parsed if chs]
        for lbl, chs in parsed:
            if not chs:
                print(f"  [!] Batch '{lbl}' matched no chapters (skipped).")
        if valid:
            return valid
        print("  [!] No chapters matched any batch. Try again.")


def _get_output_format() -> str:
    """Ask user which output format they want ('cbz', 'pdf', or 'images')."""
    pdf_note = "" if _has_pillow() else " [no Pillow]"
    print(FORMAT_MENU.format(pdf_note=pdf_note))
    formats = {1: "cbz", 2: "pdf", 3: "images"}
    while True:
        try:
            raw = input("  Select format (1-3) [1]: ").strip()
            if not raw:
                return "cbz"
            num = int(raw)
            if num in formats:
                chosen = formats[num]
                if chosen == "pdf" and not _has_pillow():
                    print("  [!] Pillow not installed. Install it first: pip install Pillow")
                    continue
                return chosen
            print("  [!] Enter 1, 2, or 3.")
        except ValueError:
            print("  [!] Enter 1, 2, or 3.")
        except (KeyboardInterrupt, EOFError):
            return "cbz"


def _get_grouping_mode() -> str:
    """Ask how multiple chapters should be grouped ('per-chapter', 'merge', or 'volumes')."""
    print(MODE_MENU)
    modes = {1: "per-chapter", 2: "merge", 3: "volumes"}
    while True:
        try:
            raw = input("  Select mode (1-3) [1]: ").strip()
            if not raw:
                return "per-chapter"
            num = int(raw)
            if num in modes:
                return modes[num]
            print("  [!] Enter 1, 2, or 3.")
        except ValueError:
            print("  [!] Enter 1, 2, or 3.")
        except (KeyboardInterrupt, EOFError):
            return "per-chapter"


# ─── Progress display ──────────────────────────────────────────────────────────

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
    print(f"  Download dir: {default}")
    try:
        raw = input("  Change directory? (y/N): ").strip().lower()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if raw not in ("y", "yes"):
        return default
    try:
        new_path = input(f"  New path [{default}]: ").strip()
    except (KeyboardInterrupt, EOFError):
        print()
        return default
    if not new_path:
        return default
    new_path = os.path.abspath(os.path.expanduser(new_path))
    os.makedirs(new_path, exist_ok=True)
    print(f"  Download dir set to: {new_path}\n")
    return new_path


# ─── Batch / merged workflows ─────────────────────────────────────────────────

def _batch_output_path(
    download_dir: str,
    safe_title: str,
    batch_label: str,
    batch_chapters: List[Tuple[float, str]],
    output_format: str,
    is_multi_batch: bool,
    batch_idx: int,
    used_names: set,
) -> str:
    series_dir = os.path.join(download_dir, safe_title)
    ext = {"cbz": ".cbz", "pdf": ".pdf", "images": ""}[output_format]

    if not is_multi_batch:
        return os.path.join(series_dir, f"{safe_title} - Merged ({len(batch_chapters)} ch){ext}")

    range_tag = _safe_dir_name(re.sub(r"\s+", "", batch_label))
    if not range_tag or len(range_tag) > 40:
        first_ch = _format_ch_label(batch_chapters[0][0]).replace("Ch ", "")
        last_ch = _format_ch_label(batch_chapters[-1][0]).replace("Ch ", "")
        range_tag = f"Ch {first_ch}-{last_ch} ({len(batch_chapters)} ch)"

    base_name = f"{safe_title} - Merged ({range_tag})"
    if base_name in used_names:
        base_name = f"{safe_title} - Merged ({range_tag} #{batch_idx})"
    used_names.add(base_name)
    return os.path.join(series_dir, f"{base_name}{ext}")


def _output_exists(out_path: str, output_format: str) -> bool:
    if output_format == "images":
        return os.path.isdir(out_path) and len(_sorted_images(out_path)) > 0
    return os.path.isfile(out_path) and os.path.getsize(out_path) > 0


def _download_temp_chapters(
    pending: List[Tuple[float, str]],
    index_map: Dict[float, int],
    total_selected: int,
    batch_id: str,
    downloaded: Dict[float, str],
    on_chapter_done,
):
    for ch_num, ch_url in pending:
        idx = index_map[ch_num]
        ch_label = _format_ch_label(ch_num)
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

        tmp_dir, ok, _ = download_chapter_temp(ch_num, images, on_progress, batch_id=batch_id)

        if ok:
            downloaded[ch_num] = tmp_dir
            sys.stdout.write(f"\r  {prefix}  {_progress_bar(page_count, page_count)}  OK\n")
            on_chapter_done()
        else:
            sys.stdout.write(f"\r  {prefix}  FAILED\n")
            sys.stdout.flush()
        sys.stdout.flush()


def _restore_batch_downloads(session_batch: dict) -> Dict[float, str]:
    restored: Dict[float, str] = {}
    raw_map = session_batch.get("downloaded", {})
    if isinstance(raw_map, dict):
        for k, td in raw_map.items():
            if td and os.path.isdir(td):
                try:
                    restored[float(k)] = td
                except ValueError:
                    pass
        return restored

    saved_chs = session_batch.get("selected_chapters", [])
    saved_tmps = session_batch.get("tmp_dirs", [])
    for ch, td in zip(saved_chs, saved_tmps):
        if td and os.path.isdir(td):
            restored[float(ch)] = td
    return restored


def _run_merged_batches(
    title: str,
    safe_title: str,
    download_dir: str,
    batches: List[Tuple[str, List[Tuple[float, str]]]],
    output_format: str = "cbz",
):
    packers = {
        "cbz": (_pack_cbz_merged, "CBZ"),
        "pdf": (_pack_pdf_merged, "PDF"),
        "images": (_pack_images_merged, "Image folder"),
    }
    packer_fn, kind_label = packers[output_format]
    is_multi = len(batches) > 1
    used_names: set = set()
    batch_targets = [
        _batch_output_path(download_dir, safe_title, label, chs, output_format, is_multi, idx, used_names)
        for idx, (label, chs) in enumerate(batches, 1)
    ]

    session = _load_session(download_dir, safe_title)
    resumed_batches: Optional[List[dict]] = None

    if session and session.get("title") == title:
        if "batches" in session and isinstance(session["batches"], list):
            cached_count = sum(
                len(_restore_batch_downloads(b))
                for b in session["batches"]
                if not b.get("done")
            )
            if cached_count > 0:
                print(f"  [~] Found previous session: {cached_count} cached chapter(s).")
                try:
                    choice = input("  Resume? (Y/n): ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    choice = "n"
                if choice != "n":
                    resumed_batches = session["batches"]
                    print(f"  Resuming with {cached_count} cached chapter(s)...\n")
        elif session.get("tmp_dirs") and not is_multi:
            restored = _restore_batch_downloads(session)
            if restored:
                print(f"  [~] Found previous session: {len(restored)} chapter(s) already downloaded.")
                try:
                    choice = input("  Resume? (Y/n): ").strip().lower()
                except (KeyboardInterrupt, EOFError):
                    choice = "n"
                if choice != "n":
                    resumed_batches = [{
                        "label": batches[0][0],
                        "downloaded": {str(k): v for k, v in restored.items()},
                        "done": False,
                        "out_path": batch_targets[0],
                    }]
                    print(f"  Resuming with {len(restored)} cached chapter(s)...\n")

    session_batches = []
    for b_idx, (label, _) in enumerate(batches):
        prev = (
            resumed_batches[b_idx]
            if resumed_batches and b_idx < len(resumed_batches) and resumed_batches[b_idx].get("label") == label
            else {}
        )
        restored = _restore_batch_downloads(prev) if prev else {}
        session_batches.append({
            "label": label,
            "done": bool(prev.get("done")) and _output_exists(batch_targets[b_idx], output_format),
            "out_path": batch_targets[b_idx],
            "downloaded": {str(k): v for k, v in restored.items()},
        })

    session_state = {"title": title, "format": output_format, "batches": session_batches}

    def persist_session():
        _save_session(download_dir, safe_title, session_state)

    saved_count = 0
    all_batches_clean = True

    for b_idx, ((label, batch_selected), out_path, sb) in enumerate(
        zip(batches, batch_targets, session_batches), 1
    ):
        if is_multi:
            print(f"\n  -- Batch {b_idx}/{len(batches)}: {label} ({len(batch_selected)} ch) --")

        if sb["done"]:
            print(f"  Already saved (skipped): {out_path}")
            saved_count += 1
            continue

        downloaded = _restore_batch_downloads(sb)
        index_map = {ch_num: i for i, (ch_num, _) in enumerate(batch_selected, 1)}
        total_selected = len(batch_selected)
        batch_id = f"b{b_idx}"

        def sync_batch():
            sb["downloaded"] = {str(k): v for k, v in downloaded.items()}
            persist_session()

        max_retries = 3
        for attempt in range(max_retries):
            pending = [(c, u) for c, u in batch_selected if c not in downloaded]
            if not pending:
                break
            if attempt > 0:
                print(f"\n  Retry attempt {attempt + 1}/{max_retries}...")
            _download_temp_chapters(pending, index_map, total_selected, batch_id, downloaded, sync_batch)

        failed = [c for c, _ in batch_selected if c not in downloaded]
        while failed:
            failed_labels = [_format_ch_label(ch) for ch in failed]
            print(f"\n  [!] {len(failed)} chapter(s) failed: {', '.join(failed_labels)}")
            try:
                choice = input("  Retry failed chapters? (Y/n): ").strip().lower()
            except (KeyboardInterrupt, EOFError):
                choice = "n"
            if choice == "n":
                break
            retry_pending = [(c, u) for c, u in batch_selected if c not in downloaded]
            _download_temp_chapters(retry_pending, index_map, total_selected, batch_id, downloaded, sync_batch)
            failed = [c for c, _ in batch_selected if c not in downloaded]
            if failed:
                print(f"\n  [!] Still {len(failed)} chapter(s) failed.")

        ordered_tmp_dirs = [downloaded[c] for c, _ in batch_selected if c in downloaded]
        if not ordered_tmp_dirs:
            all_batches_clean = False
            print(f"\n  [!] Batch '{label}' had no downloaded chapters to pack.")
            continue

        os.makedirs(os.path.dirname(out_path), exist_ok=True)

        def on_pack_progress(done: int, total: int):
            bar = _progress_bar(done, total)
            sys.stdout.write(f"\r  Packing pages  {bar}")
            sys.stdout.flush()

        print(f"\n  Packing {len(ordered_tmp_dirs)} chapter(s) into {os.path.basename(out_path)}...")
        pack_ok, pack_log = packer_fn(ordered_tmp_dirs, out_path, on_pack_progress)

        if pack_ok:
            for td in ordered_tmp_dirs:
                shutil.rmtree(td, ignore_errors=True)
            sb["done"] = True
            sb["downloaded"] = {}
            persist_session()
            saved_count += 1
            sys.stdout.write(f"\r  Packing pages  {_progress_bar(1, 1)}  OK\n")
            print(f"\n  Saved: {out_path}")
            print(f"    ({len(ordered_tmp_dirs)}/{total_selected} chapters, {pack_log})")
            if failed:
                all_batches_clean = False
                print(f"    [!] {len(failed)} chapter(s) skipped: {', '.join(_format_ch_label(c) for c in failed)}")
        else:
            all_batches_clean = False
            print(f"\n  [!] Packing failed: {pack_log}")
            print("  Temp files kept — you can resume next time.")

    if all_batches_clean:
        _clear_session(download_dir, safe_title)

    if is_multi:
        print(f"\n  Done: {saved_count}/{len(batches)} {kind_label}(s) saved to: {os.path.join(download_dir, safe_title)}")


def _run_per_chapter_downloads(
    title: str,
    safe_title: str,
    download_dir: str,
    selected: List[Tuple[float, str]],
    output_format: str,
    ext_label: str,
):
    ok_count = 0
    for i, (ch_num, ch_url) in enumerate(selected, 1):
        ch_label = _format_ch_label(ch_num)
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
        print(f"  All {ok_count} chapter(s) saved as {ext_label} to: {os.path.join(download_dir, safe_title)}")
    else:
        print(f"  {ok_count}/{len(selected)} chapter(s) saved.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        prog="mangadl",
        description="mangadl — manga downloader for weebcentral.com",
        add_help=True,
    )
    parser.add_argument(
        "--dir",
        metavar="PATH",
        default=None,
        help="Set download directory (default: current directory)",
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
        download_dir = os.path.abspath(os.path.expanduser(args.dir))
        os.makedirs(download_dir, exist_ok=True)
        print(f"  Download dir: {download_dir}\n")
    else:
        download_dir = os.path.abspath(
            os.path.expanduser(
                os.environ.get("MANGA_DOWNLOAD_DIR", os.getcwd())
            )
        )
        download_dir = _pick_download_dir(download_dir)

    while True:
        manga = _search_manga()
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
        print(f"  Found {total_ch} chapters  (Ch {ch_min:.0f} - Ch {ch_max:.0f})")

        raw_range = _get_chapter_range(ch_min, ch_max)
        if not raw_range:
            continue

        parsed_batches = parse_batch_ranges(chapters, raw_range)
        batches = [(lbl, chs) for lbl, chs in parsed_batches if chs]
        for lbl, chs in parsed_batches:
            if not chs:
                print(f"  [!] Batch '{lbl}' matched no chapters (skipped).")

        if not batches:
            print("  [!] No chapters matched that range.")
            continue

        has_star = "*" in raw_range
        if len(batches) > 1:
            print(f"\n  Batches ({len(batches)}):")
            for idx, (lbl, chs) in enumerate(batches, 1):
                c0 = _format_ch_label(chs[0][0])
                c1 = _format_ch_label(chs[-1][0])
                print(f"    {idx}. {lbl} ({len(chs)} ch: {c0} - {c1})")

        output_format = _get_output_format()

        if has_star or len(batches) > 1:
            mode = "volumes"
        elif len(batches[0][1]) == 1:
            mode = "per-chapter"
        else:
            mode = _get_grouping_mode()
            if mode == "volumes":
                batches = _prompt_volume_batches(chapters, batches)

        ext_labels = {"cbz": "CBZ", "pdf": "PDF", "images": "Images"}
        ext_label = ext_labels[output_format]
        safe_title = _safe_dir_name(title)
        series_out = os.path.join(download_dir, safe_title)

        if mode in ("merge", "volumes"):
            total_to_dl = sum(len(chs) for _, chs in batches)
            mode_label = f"{len(batches)} volume(s)" if len(batches) > 1 else "All-in-One"
            print(f"\n  Downloading {total_to_dl} ch -> {ext_label} ({mode_label})")
            print(f"  Saving to: {series_out}")
            print(f"  Downloader: {'aria2c' if _has_aria2c() else 'threaded'}\n")
            _run_merged_batches(title, safe_title, download_dir, batches, output_format)
        else:
            selected = batches[0][1]
            print(f"\n  Downloading {len(selected)} chapter(s) -> {ext_label}")
            print(f"  Saving to: {series_out}")
            print(f"  Downloader: {'aria2c' if _has_aria2c() else 'threaded'}\n")
            _run_per_chapter_downloads(title, safe_title, download_dir, selected, output_format, ext_label)

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
