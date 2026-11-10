# mangadl-cli

Fast CLI manga downloader for [weebcentral.com](https://weebcentral.com). Works on Android (Termux), Windows, Linux, and macOS.

## Features

- Search manga and pick from a numbered list
- Flexible chapter selection (`1`, `2-8`, `1,5,10`, `all`, or `*`-separated volumes)
- **CBZ**, **PDF**, and **Images** output formats
- **Per Chapter**, **All-in-One**, and **By Volume (`*`)** grouping modes
- Fast `aria2c` downloads with automatic built-in threaded fallback
- Auto-retry and session resume for merged/volume batches

## Install

```bash
pip install mangadl-cli
```

Optional `aria2` for faster downloads:

- **Termux:** `pkg install aria2` (run `termux-setup-storage` for `/sdcard` access)
- **Debian / Ubuntu:** `sudo apt install aria2`
- **macOS:** `brew install aria2`
- **Windows:** [aria2](https://aria2.github.io/) or `pip install aria2`

## Usage

```bash
mangadl
mangadl --dir /path/to/manga
```

1. Search and select a manga from the numbered list
2. Enter a chapter range (e.g. `1`, `2-8`, `1-10,20`, `all`, or `1-10,15 * 24-30,32` for volumes)
3. Choose output format (`CBZ`, `PDF`, `Images`) and mode (`Per Chapter`, `All-in-One`, `By Volume (*)`)

## Options

- `--dir PATH` — set download directory (default: current directory)
- `MANGA_DOWNLOAD_DIR` — environment variable to override the default download directory
