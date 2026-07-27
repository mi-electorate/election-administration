"""
Barcode Lookup Table Viewer
============================

A single-file Python/Tkinter application that:
  1. Loads a CSV file (correctly handling quoted fields with embedded newlines)
  2. Displays it as a sortable table
  3. Accepts input from a USB barcode scanner (which behaves as a keyboard)
  4. Finds the record matching the scanned value in a chosen column
  5. Highlights that record's row in the table

Coming from C: the biggest mental shift here is that this program is
*event-driven*. Instead of a top-to-bottom loop you control, you register
callback functions (like on_scan, on_open_csv) and Tkinter calls them
whenever the relevant event happens (a keypress, a button click, etc).
The mainloop() call at the bottom is the "listener" that waits for events
forever until the window is closed.

No third-party libraries are required -- everything here is in the Python
standard library, which keeps PyInstaller packaging simple.
"""

import csv
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class BarcodeLookupApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Barcode Lookup Table Viewer")
        self.root.geometry("900x550")

        # ---- Data state ----
        self.records = []          # list of dicts, one per CSV row
        self.fieldnames = []       # CSV column headers, in order
        self.index_by_value = {}   # match-column value -> row index, rebuilt on load
        self.row_id_by_index = {}  # row index -> Treeview item id, rebuilt on load

        self._build_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self):
        # --- Top control bar: Open CSV, choose match column, status ---
        top_frame = tk.Frame(self.root, padx=8, pady=8)
        top_frame.pack(side=tk.TOP, fill=tk.X)

        open_btn = tk.Button(top_frame, text="Open CSV...", command=self.on_open_csv)
        open_btn.pack(side=tk.LEFT)

        tk.Label(top_frame, text="   Match column:").pack(side=tk.LEFT)
        self.match_column_var = tk.StringVar()
        self.match_column_dropdown = ttk.Combobox(
            top_frame, textvariable=self.match_column_var, state="readonly", width=20
        )
        self.match_column_dropdown.pack(side=tk.LEFT, padx=(0, 12))
        self.match_column_dropdown.bind("<<ComboboxSelected>>", self.on_match_column_changed)

        # --- Barcode scan entry box ---
        tk.Label(top_frame, text="Scan barcode:").pack(side=tk.LEFT)
        self.scan_var = tk.StringVar()
        self.scan_entry = tk.Entry(top_frame, textvariable=self.scan_var, width=30)
        self.scan_entry.pack(side=tk.LEFT, padx=(4, 0))
        # Barcode scanners "type" the code then send Enter (Return) as the
        # last keystroke. Binding <Return> on this Entry is all we need --
        # no special driver, since the scanner is just a fast keyboard.
        self.scan_entry.bind("<Return>", self.on_scan)

        # --- Table (Treeview) ---
        table_frame = tk.Frame(self.root)
        table_frame.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        self.tree = ttk.Treeview(table_frame, show="headings")
        vsb = ttk.Scrollbar(table_frame, orient="vertical", command=self.tree.yview)
        hsb = ttk.Scrollbar(table_frame, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=vsb.set, xscrollcommand=hsb.set)

        self.tree.grid(row=0, column=0, sticky="nsew")
        vsb.grid(row=0, column=1, sticky="ns")
        hsb.grid(row=1, column=0, sticky="ew")
        table_frame.rowconfigure(0, weight=1)
        table_frame.columnconfigure(0, weight=1)

        # Row highlight color for a matched scan
        self.tree.tag_configure("match", background="#ffe066")

        # --- Status bar ---
        self.status_var = tk.StringVar(value="Open a CSV file to begin.")
        status_bar = tk.Label(
            self.root, textvariable=self.status_var, bd=1, relief=tk.SUNKEN, anchor="w"
        )
        status_bar.pack(side=tk.BOTTOM, fill=tk.X)

        # Keep the scan box focused so scans land there even after clicking
        # elsewhere in the window (barcode scanners can't "click" a text box).
        self.root.after(200, self._refocus_scan_entry)

    def _refocus_scan_entry(self):
        # Only steal focus back if nothing else that needs typed input has it
        # (e.g. don't yank focus while the user is editing the match-column
        # dropdown). This is a light-touch convenience, not a hard lock.
        if self.root.focus_get() in (None, self.root):
            self.scan_entry.focus_set()
        self.root.after(500, self._refocus_scan_entry)

    # ------------------------------------------------------------------
    # CSV loading
    # ------------------------------------------------------------------
    def on_open_csv(self):
        path = filedialog.askopenfilename(
            title="Open CSV file",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if not path:
            return
        self.load_csv(path)

    def load_csv(self, path):
        try:
            # newline='' is required so the csv module -- not Python's own
            # universal-newline handling -- controls how newlines inside
            # quoted fields are interpreted. Without it, multiline quoted
            # fields can be parsed incorrectly.
            with open(path, newline="", encoding="utf-8-sig") as f:
                reader = csv.DictReader(f)
                self.fieldnames = reader.fieldnames or []
                self.records = list(reader)
        except Exception as exc:
            messagebox.showerror("Error opening CSV", f"Could not read file:\n{exc}")
            return

        if not self.fieldnames:
            messagebox.showerror("Error opening CSV", "No columns found in this file.")
            return

        self._populate_table()
        self._populate_match_column_dropdown()
        self.status_var.set(f"Loaded {len(self.records)} records from {path}")

    def _populate_table(self):
        # Reset the Treeview's columns to match the new CSV's headers
        self.tree.delete(*self.tree.get_children())
        self.tree["columns"] = self.fieldnames
        for col in self.fieldnames:
            self.tree.heading(col, text=col)
            self.tree.column(col, width=120, anchor="w")

        self.row_id_by_index = {}
        for i, record in enumerate(self.records):
            values = [record.get(col, "") for col in self.fieldnames]
            # Treeview stores multiline strings on one visual line by
            # default; replace embedded newlines with a visible marker so
            # multiline quoted fields don't look broken in the grid.
            values = [v.replace("\n", " / ").replace("\r", "") for v in values]
            item_id = self.tree.insert("", "end", iid=str(i), values=values)
            self.row_id_by_index[i] = item_id

    def _populate_match_column_dropdown(self):
        self.match_column_dropdown["values"] = self.fieldnames
        # Default to the first column; user can change it any time.
        self.match_column_var.set(self.fieldnames[0])
        self._rebuild_match_index()

    def on_match_column_changed(self, event=None):
        self._rebuild_match_index()

    def _rebuild_match_index(self):
        column = self.match_column_var.get()
        if not column:
            self.index_by_value = {}
            return
        # Build value -> row index lookup once, so every scan afterwards
        # is an O(1) dictionary lookup instead of scanning the whole list.
        self.index_by_value = {}
        for i, record in enumerate(self.records):
            value = (record.get(column) or "").strip()
            self.index_by_value[value] = i

    # ------------------------------------------------------------------
    # Barcode scan handling
    # ------------------------------------------------------------------
    def on_scan(self, event=None):
        scanned_value = self.scan_var.get().strip()
        self.scan_var.set("")  # clear the box for the next scan

        if not self.records:
            self.status_var.set("No CSV loaded yet.")
            return
        if not scanned_value:
            return

        # Clear any previous highlight
        for item_id in self.tree.get_children():
            self.tree.item(item_id, tags=())

        row_index = self.index_by_value.get(scanned_value)
        if row_index is None:
            self.status_var.set(f'No match found for "{scanned_value}"')
            self._flash_scan_entry("#ffb3b3")  # light red flash
            return

        item_id = self.row_id_by_index[row_index]
        self.tree.item(item_id, tags=("match",))
        self.tree.see(item_id)             # scroll so the match is visible
        self.tree.selection_set(item_id)
        self.status_var.set(f'Match found for "{scanned_value}" (row {row_index + 1})')

    def _flash_scan_entry(self, color, times=3):
        # Simple visual "no match" feedback: flash the entry box background.
        original = self.scan_entry.cget("background")

        def toggle(n):
            if n <= 0:
                self.scan_entry.configure(background=original)
                return
            current = self.scan_entry.cget("background")
            next_color = color if current == original else original
            self.scan_entry.configure(background=next_color)
            self.root.after(150, lambda: toggle(n - 1))

        toggle(times * 2)


def main():
    root = tk.Tk()
    app = BarcodeLookupApp(root)

    # Allow "python barcode_lookup.py mydata.csv" to open a file on launch
    if len(sys.argv) > 1:
        app.load_csv(sys.argv[1])

    root.mainloop()


if __name__ == "__main__":
    main()
