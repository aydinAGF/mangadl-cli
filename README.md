# mangadl-cli

A manga downloader CLI that downloads from [weebcentral.com](https://weebcentral.com). Works on **Android (Termux)**, **Windows**, **Linux**, and **macOS**.

---

## Features

- **Live search** — type manga name, get suggestions as you type
- **Chapter range selection** — single, range, batch, mixed
- **Multiple output formats** — CBZ, PDF, Images, or merged All-in-One CBZ
- **Fast downloads** — aria2c multi-connection downloading
- **Resume support** — interrupted downloads continue from where they stopped
- **Auto-retry** — failed chapters retry automatically
- **Cross-platform** — same command on all platforms

---

## Install

### Android (Termux)

```bash
# Update packages
pkg update && pkg upgrade

# Install dependencies
pkg install aria2 python

# Install mangadl-cli
pip install mangadl-cli
```

Grant storage access so mangadl can save to your gallery:
```bash
termux-setup-storage
```

### Windows

1. Install Python from https://www.python.org/downloads/ (check "Add to PATH")
2. Open Command Prompt or PowerShell:
```bash
pip install mangadl-cli
```

Optional — install aria2 for faster downloads: https://aria2.github.io/

### Linux (Debian/Ubuntu)

```bash
# Install dependencies
sudo apt update
sudo apt install python3-pip aria2

# Install mangadl-cli
pip install mangadl-cli
```

### macOS

```bash
# Install dependencies
brew install aria2

# Install mangadl-cli
pip3 install mangadl-cli
```

---

## Usage

```bash
mangadl
```

1. Type manga name — suggestions appear as you type
2. Pick a result from the numbered list
3. Enter chapter range (see format below)
4. Choose output format
5. Downloads start automatically

### Command-line options

```bash
# Set custom download directory
mangadl --dir /path/to/manga

# Show help
mangadl --help
```

---

## Output formats

| Format | Description |
|--------|-------------|
| **CBZ** | Comic book archive — one file per chapter, best for comic readers |
| **PDF** | Single PDF per chapter (requires `pip install Pillow`) |
| **Images** | Raw image folder — one folder per chapter |
| **CBZ (All-in-One)** | Merge all selected chapters into one big CBZ file |

### All-in-One CBZ

When you select "CBZ (All-in-One)", mangadl:
1. Downloads all selected chapters to temp files
2. Numbers pages sequentially across chapters (0001, 0002, ...)
3. Merges everything into a single CBZ

Example: Chapter 1 (20 pages) + Chapter 2 (15 pages) = one CBZ with 35 pages numbered 0001-0035.

---

## Chapter range formats

| Input | Meaning |
|-------|---------|
| `1` | Single chapter 1 |
| `2-8` | Chapters 2 through 8 |
| `1-90` | Chapters 1 through 90 |
| `all` | Every chapter |
| `1,5,10` | Specific chapters |
| `1-10,20,30-35` | Mixed ranges |

---

## Default download locations

| Platform | Default path |
|----------|-------------|
| Android (Termux) | `/sdcard/Manga/` |
| Windows | `C:\Users\<you>\Downloads\Manga\` |
| Linux | `~/Downloads/Manga/` |
| macOS | `~/Downloads/Manga/` |

You can change the directory:
- At startup when prompted
- With the `--dir` flag: `mangadl --dir /custom/path`
- With environment variable: `MANGA_DOWNLOAD_DIR=/path mangadl`

---

## Requirements

| Tool | Required? | Install |
|------|-----------|---------|
| Python 3.8+ | Yes | Pre-installed on most systems |
| aria2 | Recommended | `pkg install aria2` / `apt install aria2` / `brew install aria2` |
| prompt_toolkit | Yes (included) | Installed automatically |
| Pillow | Optional | `pip install Pillow` (for PDF output) |

Without aria2, mangadl uses built-in threaded downloads (slower but works).

---

## Troubleshooting

### aria2c not found
```bash
# Termux
pkg install aria2

# Windows — download from https://aria2.github.io/
# Linux
sudo apt install aria2

# macOS
brew install aria2
```

### Permission denied on /sdcard (Termux)
```bash
termux-setup-storage
```

### Python not found (Windows)
Make sure you checked "Add Python to PATH" during install. Then restart your terminal.

### Slow downloads
Install aria2 for faster multi-connection downloading.

---

## License

MIT
