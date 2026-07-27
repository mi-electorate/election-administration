# Barcode Lookup Table Viewer

A single-file Python/Tkinter app that loads a CSV, displays it as a table,
and highlights the matching row when you scan a barcode.

## Running it directly (for development, e.g. on Linux)

Requires only Python 3 — no extra packages, since it uses just the
standard library (`csv`, `tkinter`).

```bash
python3 barcode_lookup.py
# or open a file immediately:
python3 barcode_lookup.py mydata.csv
```

If `tkinter` isn't installed on your Linux distro, install it via your
package manager, e.g. on Debian/Ubuntu:
```bash
sudo apt install python3-tk
```

## Using the app

1. Click **Open CSV...** and pick your file.
2. Choose the **Match column** — the CSV column that your barcode values
   correspond to (e.g. SKU, ID, Serial Number). Defaults to the first
   column.
3. Click into the **Scan barcode** box (it auto-refocuses itself, so you
   normally don't need to) and scan. The scanner "types" the code and
   hits Enter for you — no special driver needed since it behaves like a
   keyboard.
4. The matching row highlights in yellow and scrolls into view. If
   nothing matches, the scan box flashes red and the status bar explains.

## Building a single-file Windows .exe (from your Linux machine)

PyInstaller cannot cross-compile — a build run on Linux produces a Linux
binary, not a Windows one. The included GitHub Actions workflow
(`.github/workflows/build.yml`) solves this by building on an actual
Windows machine in the cloud, for free, every time you push.

### One-time setup

1. Create a new GitHub repository and push this folder to it:
   ```bash
   cd project
   git init
   git add .
   git commit -m "Initial commit"
   git branch -M main
   git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
   git push -u origin main
   ```
2. On GitHub, go to the **Actions** tab of your repo. The "Build Windows
   EXE" workflow should already be running (it triggers on push).

### Getting the .exe

1. Wait for the workflow run to finish (green checkmark, usually ~1 minute).
2. Click into that run, scroll to **Artifacts**, and download
   `BarcodeLookup-windows-exe`. It's a zip containing `BarcodeLookup.exe`.
3. Copy that `.exe` to the Windows machine — it's fully standalone, no
   Python installation needed there.

### Rebuilding after changes

Just commit and push again:
```bash
git add barcode_lookup.py
git commit -m "Update app"
git push
```
The workflow reruns automatically and a fresh `.exe` artifact appears.

You can also trigger a build manually anytime from the Actions tab
("Run workflow" button) without needing a code change.

## Notes / things you may want to customize

- **Highlight color**: change `background="#ffe066"` in
  `tree.tag_configure("match", ...)`.
- **No-match behavior**: currently flashes the scan box red and updates
  the status bar. See `on_scan()` / `_flash_scan_entry()` to change this
  (e.g. add a beep with `self.root.bell()`).
- **Multiline field display**: embedded newlines in the CSV are shown as
  ` / ` in the table for readability. Change this in `_populate_table()`
  if you'd rather see them differently.
- **CSV encoding**: assumes UTF-8 (with or without a BOM). If your CSVs
  come from Excel on Windows and show odd characters, try changing
  `encoding="utf-8-sig"` to `encoding="cp1252"` in `load_csv()`.
