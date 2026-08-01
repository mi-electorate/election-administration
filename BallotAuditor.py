"""
Ballot Auditor
==============

A single-file Python/Tkinter application that:
  - Loads a CSV generated from Michigan's QVF program
    - CSV file must contain the columns VoterID, BallotNumber and DateReturned
  - Sorts on ballot received date and number
  - Displays sorted results as a table
  - Takes VOTERID input (e.g. from from a USB barcode scanner)
  - Highlights input VOTERID records in the table
  - Tracks count of records found and not found
  - throws a warning if the VOTERID is not found, e.g. ballot in wrong precinct
  - notifies user at every bundle of (default) 50 ballots (set by BUNDLE_SIZE)
  - allows user to click a record to see full details in a popup window

  set SCAN_DEBUG_LOG_ENABLED = True for a scan debug log in the local path

"""

import csv
import sys
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# winsound is Windows-only (this app's deployment target) and gives a
# more reliably audible alert than Tk's generic bell(), which depends on
# the OS/desktop environment's system-bell settings -- on Linux (used for
# development) these are frequently muted or disabled by default even
# when bell() itself works correctly.
try:
    import winsound
except ImportError:
    winsound = None

# Need these columns 
REQUIRED_COLUMNS = ["VoterID", "BallotNumber", "DateReturned"]

# Extra columns shown in the compact table alongside the required ones,
# purely for context -- unlike REQUIRED_COLUMNS, it's not an error if
# these are missing from a given CSV; they're just skipped.
OPTIONAL_DISPLAY_COLUMNS = ["Precinct", "BallotID"]

# How many successful scans make up one ballot bundle -- a popup
# announces every multiple of this (50, 100, 150...).
BUNDLE_SIZE = 50

# Accepted formats for the DateReturned column. The au3 version assumed
# M/D/YYYY (optionally followed by a time, which it discarded) -- kept as
# the first format tried; a couple of common variants are added as a
# fallback rather than hard-failing on them.
DATE_RETURNED_FORMATS = ["%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d"]

# Many USB "keyboard wedge" barcode scanners emit digits over the numeric
# keypad rather than the top-row number keys (it's keyboard-layout
# independent). Tk translates keypad keys to a keysym based on what it
# thinks the NumLock state is -- and that tracking is unreliable, so the
# same physical "5" key can arrive as either the digit keysym (KP_5) or
# the NumLock-off navigation keysym (KP_Begin), unpredictably, sometimes
# switching mid-scan. A plain text editor doesn't have this problem
# because it just displays the character the OS already composed; Tk's
# default Entry binding doesn't know what to do with the navigation
# keysyms, so those keystrokes get silently dropped or move the cursor
# instead of typing a digit -- which is what produces "garbled" scans.
# Mapping every keypad keysym (both forms) straight to its digit and
# inserting it ourselves (see _on_scan_keypress) sidesteps the
# ambiguity entirely.
KEYPAD_DIGIT_MAP = {
    "KP_0": "0", "KP_Insert": "0",
    "KP_1": "1", "KP_End": "1",
    "KP_2": "2", "KP_Down": "2",
    "KP_3": "3", "KP_Next": "3",
    "KP_4": "4", "KP_Left": "4",
    "KP_5": "5", "KP_Begin": "5",
    "KP_6": "6", "KP_Right": "6",
    "KP_7": "7", "KP_Home": "7",
    "KP_8": "8", "KP_Up": "8",
    "KP_9": "9", "KP_Prior": "9",
}

# A real VoterID barcode is digits only. Anything else landing in the scan
# box is either a human typo or -- far more likely during an actual scan --
# a corrupted keystroke (see _on_scan_keypress). Rather than let a stray
# letter/symbol silently become part of the "scanned" value, we refuse to
# insert it at all. These are the *non-digit* keys that still need to work
# normally for a human editing the box by hand.
ALLOWED_EDITING_KEYSYMS = {
    "BackSpace", "Delete", "Left", "Right", "Home", "End", "Tab",
    "Return", "KP_Enter",
}
# Modifier keys themselves must be let through untouched (not treated as
# "not a digit, so reject") or combinations like Ctrl+A / Shift+Home would
# stop working.
MODIFIER_KEYSYMS = {
    "Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R",
    "Caps_Lock", "Num_Lock", "Super_L", "Super_R", "Meta_L", "Meta_R",
}

# Forensic log of every raw keystroke Tk actually delivered for each scan
# attempt, written out after the fact (never during the keystroke burst
# itself, so logging can't be the thing that slows Tk down). When a scan
# comes out wrong, this is the difference between "the OS/driver dropped a
# character before Tk ever saw it" (nothing the app can fix) and "Tk saw
# it and mis-decoded it" (something we can potentially still fix) --
# there's no way to tell those apart from the final garbled value alone.
#
# Anchored to the script's own folder (or the .exe's folder, if frozen
# with PyInstaller etc) rather than left as a bare relative path -- a
# relative path resolves against the process's *current working
# directory*, which depends on how the app was launched (double-clicked,
# via a shortcut with a "Start in" folder, from a terminal, from an
# IDE...) and can easily end up somewhere the operator never finds.
if getattr(sys, "frozen", False):
    _APP_DIR = Path(sys.executable).resolve().parent
else:
    _APP_DIR = Path(__file__).resolve().parent
# Flip to False for production -- disables the keystroke debug log
# entirely (nothing appended to _scan_keystroke_log, nothing written to
# SCAN_DEBUG_LOG_PATH). Handy to keep the diagnostic capability in the
# codebase without shipping a log file that grows on every deployed
# machine.
SCAN_DEBUG_LOG_ENABLED = False

SCAN_DEBUG_LOG_PATH = _APP_DIR / "scan_debug.log"


class BallotAuditor:
    def __init__(self, root):
        self.root = root
        self.root.title("Ballot Auditor")

        # Start maximized so the table gets as much screen space as
        # possible. "zoomed" is the Windows-native maximized state (this
        # app's deployment target) -- it keeps the title bar and taskbar
        # visible, unlike fullscreen, and the window stays user-resizable
        # afterward. Other platforms (e.g. Linux, used for development)
        # don't support "zoomed", so fall back to sizing the window to
        # most of the actual screen and centering it.
        try:
            self.root.state("zoomed")
        except tk.TclError:
            screen_w = self.root.winfo_screenwidth()
            screen_h = self.root.winfo_screenheight()
            width = int(screen_w * 0.9)
            height = int(screen_h * 0.9)
            x = (screen_w - width) // 2
            y = (screen_h - height) // 2
            self.root.geometry(f"{width}x{height}+{x}+{y}")

        # ---- Data state ----
        self.records = []          # list of dicts, one per CSV row
        self.fieldnames = []       # CSV column headers, in order
        self.index_by_value = {}   # match-column value -> row index, rebuilt on load
        self.row_id_by_index = {}  # row index -> Treeview item id, rebuilt on load
        self.matched_indices = set()  # row indices scanned/matched so far this session
        self.not_found_values = set()  # scanned values already counted as not-found this session
        self.display_columns = []  # [(label, actual_header_key), ...] for the compact table
        self.voter_id_column = None  # actual header name resolved for VoterID (case varies)
        self.voter_id_lengths = set()  # distinct VoterID lengths in the loaded CSV, rebuilt on load
        self.found_count = 0      # unique matched records scanned so far
        self.not_found_count = 0  # unique unrecognized codes scanned so far

        # Raw (time, keysym) log for the scan currently being typed, reset
        # every time the box empties out (a fresh scan started) and
        # flushed to SCAN_DEBUG_LOG_PATH once Enter arrives -- see
        # _on_scan_keypress and on_scan.
        self._scan_keystroke_log = []

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

        # --- Barcode scan entry box ---
        tk.Label(top_frame, text="Scan barcode:").pack(side=tk.LEFT)
        self.scan_var = tk.StringVar()
        self.scan_entry = tk.Entry(top_frame, textvariable=self.scan_var, width=30)
        self.scan_entry.pack(side=tk.LEFT, padx=(4, 0))
        # Barcode scanners "type" the code then send Enter (Return) as the
        # last keystroke. Binding <Return> on this Entry is all we need --
        # no special driver, since the scanner is just a fast keyboard.
        self.scan_entry.bind("<Return>", self.on_scan)
        # See KEYPAD_DIGIT_MAP / ALLOWED_EDITING_KEYSYMS above: intercept
        # ambiguous numeric-keypad keysyms and non-digit characters before
        # Tk's default Entry binding gets a chance to mishandle or accept
        # them, and log every raw keystroke for forensic purposes.
        self.scan_entry.bind("<KeyPress>", self._on_scan_keypress)

        # --- Right side of the top bar: running counts + About ---
        # Packed in this order (About first) so side=RIGHT stacks them
        # left of About, giving the visual order: Found | Not Found | About
        about_btn = tk.Button(top_frame, text="About", command=self.show_about)
        about_btn.pack(side=tk.RIGHT, padx=(8, 0))

        self.not_found_count_var = tk.StringVar(value="Not Found: 0")
        tk.Label(top_frame, textvariable=self.not_found_count_var, fg="#c0392b").pack(
            side=tk.RIGHT, padx=(8, 0)
        )

        self.found_count_var = tk.StringVar(value="Found: 0")
        tk.Label(top_frame, textvariable=self.found_count_var, fg="#1a7d3a").pack(
            side=tk.RIGHT, padx=(8, 0)
        )

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
        self.tree.tag_configure("match", background="#90ee90")

        # Click any data row to see every field (not just the compact
        # columns) in a popup -- the click always fires alongside the
        # Treeview's normal built-in selection behavior, not instead of it.
        self.tree.bind("<Button-1>", self._on_row_click)

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
        # The scan box should always have keyboard focus -- it's the only
        # place typed/scanned input is meaningful in this app -- EXCEPT
        # while a modal dialog (Open CSV, the not-found warning) is
        # actively grabbing input, where stealing focus back would break
        # using that dialog.
        #
        # This used to only reclaim focus if NOTHING had it (focus was
        # None or the root window). That missed the real problem: any
        # other widget grabbing focus -- e.g. clicking a table row, which
        # Tk's default click handling focuses onto the Treeview -- kept
        # focus there indefinitely, since a focused Treeview is neither
        # None nor root. ttk.Treeview has its own built-in "type to
        # search" behavior bound to typed characters, so a barcode
        # scanned while it had focus never reached on_scan at all --
        # instead it silently drove the Treeview's own incremental
        # search, jumping the selection to whatever row happened to
        # match. That's what produced "the same scan gives different
        # results each time."
        #
        # grab_current() can raise KeyError in the same edge case as
        # focus_get() used to (a native dialog Tkinter's binding doesn't
        # recognize) -- treat that as "something has a grab, don't
        # interfere" to be safe.
        try:
            grabbing_widget = self.root.grab_current()
        except KeyError:
            grabbing_widget = "unknown"

        if grabbing_widget is None:
            self.scan_entry.focus_set()
        self.root.after(500, self._refocus_scan_entry)

    # ------------------------------------------------------------------
    # CSV loading
    # ------------------------------------------------------------------
    def show_about(self):
        log_line = f"Scan debug log: {SCAN_DEBUG_LOG_PATH}" if SCAN_DEBUG_LOG_ENABLED else "Scan debug log: disabled"
        messagebox.showinfo(
            "Ballot Auditor v2.0",
            "Ballot Auditor\n\n"
            "Loads and sorts a QVF CSV,  displays it as a sortable table. "
            "Highlights given VoterID records."
            "Click any row in the table to see its full record details.\n\n"
            f"{log_line}",
        )

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
                # This export format starts with 3 junk lines before the
                # real header row (line 4) -- skip them with plain
                # readline() before handing the rest of the file to
                # DictReader. This assumes those 3 lines are simple
                # metadata/titles with no embedded newlines inside quotes;
                # if that ever changes, this would need to skip using the
                # csv reader itself instead (row-aware, not line-aware).
                JUNK_HEADER_LINES = 3
                for _ in range(JUNK_HEADER_LINES):
                    f.readline()

                reader = csv.DictReader(f)
                self.fieldnames = reader.fieldnames or []
                self.records = list(reader)
        except Exception as exc:
            messagebox.showerror("Error opening CSV", f"Could not read file:\n{exc}")
            return

        if not self.fieldnames:
            messagebox.showerror("Error opening CSV", "No columns found in this file.")
            return

        try:
            self.records = self.synthesize_table(self.records, self.fieldnames)
        except ValueError as exc:
            messagebox.showerror("File Format Mismatch", str(exc))
            self.records = []
            self.fieldnames = []
            self.tree.delete(*self.tree.get_children())
            self.status_var.set("Load failed -- see error above.")
            return

        self._populate_table()
        self._rebuild_match_index()
        self.status_var.set(f"Loaded {len(self.records)} records from {path}")

    def synthesize_table(self, records, fieldnames):
        """
        Ported from VoterBallotAuditor.au3: VerifyFileFormat (column check),
        SortDateBarCodes (date+ballot sort key), and the field-selection
        half of CreateAuditFile (only VoterID/BallotNumber/DateReturned are
        ever displayed downstream, even though the source CSV has ~19
        columns).

        Raises:
            ValueError: if a required column is missing. The message is
            meant to be shown to the user directly (mirrors the au3
            version's "File Format Mismatch" / "Data Mismatch" MsgBox).
        """
        column_map = self._match_required_columns(fieldnames)

        # ---- Build (date, ballot_number) sort keys ----
        # The au3 version concatenated "YYYYMMDD" + "." + BallotNumber as a
        # *string* and sorted that -- which sorts ballot "10" before "5"
        # (string comparison, not numeric). Sorting on real (date, number)
        # values instead fixes that; remove the ballot_sort_key() call
        # below (and just use ballot_str) if you'd rather match the
        # original string-sort behavior exactly.
        unparseable_dates = []
        annotated = []
        for record in records:
            date_str = (record.get(column_map["DateReturned"]) or "").strip()
            ballot_str = (record.get(column_map["BallotNumber"]) or "").strip()

            parsed_date = self._parse_date_returned(date_str)
            if parsed_date is None and date_str:
                unparseable_dates.append(date_str)

            sort_key = (
                parsed_date or datetime.max,  # unparseable/blank dates sort last
                self._ballot_sort_key(ballot_str),
            )
            annotated.append((sort_key, record))

        if unparseable_dates:
            # Non-fatal -- unlike the au3 version, which aborted the whole
            # program on a bad row (ExitLoop + Exit($ErrorLevel)). Bad rows
            # here just sort to the end instead of taking the app down;
            # flip this to `raise ValueError(...)` if you'd rather match
            # the original's hard-stop behavior.
            sample = ", ".join(unparseable_dates[:5])
            self.status_var.set(
                f"Warning: {len(unparseable_dates)} row(s) had an unrecognized "
                f"DateReturned value (e.g. \"{sample}\") and were sorted last."
            )

        annotated.sort(key=lambda pair: pair[0])

        # ---- Keep all original columns in the data, but pick a compact
        # ---- subset for the table itself ----
        # Full records/fieldnames are preserved unchanged (aside from
        # sorting) so the click-to-expand popup can show every field. The
        # table view itself only shows a handful of columns for context --
        # see display_columns, used by _populate_table.
        self.voter_id_column = column_map["VoterID"]

        lower_to_actual = {name.strip().lower(): name for name in fieldnames}
        self.display_columns = [
            ("VoterID", column_map["VoterID"]),
            ("BallotNumber", column_map["BallotNumber"]),
            ("DateReturned", column_map["DateReturned"]),
        ]
        for optional in OPTIONAL_DISPLAY_COLUMNS:
            actual = lower_to_actual.get(optional.lower())
            if actual is not None:
                self.display_columns.append((optional, actual))

        new_records = [record for _sort_key, record in annotated]
        return new_records

    def _match_required_columns(self, fieldnames):
        """
        Case-insensitively match REQUIRED_COLUMNS against the CSV's actual
        headers. Returns a dict mapping canonical name -> actual header
        name found in the file (so records[actual_header] still works).

        Raises ValueError listing exactly what's missing, mirroring the
        au3 version's "File Format Mismatch" / "Data Mismatch" MsgBox text.
        """
        lower_to_actual = {name.strip().lower(): name for name in fieldnames}
        column_map = {}
        missing = []
        for required in REQUIRED_COLUMNS:
            actual = lower_to_actual.get(required.lower())
            if actual is None:
                missing.append(required)
            else:
                column_map[required] = actual

        if missing:
            raise ValueError(
                "This CSV is missing required column(s): "
                + ", ".join(missing)
                + f"\n\nColumns found: {', '.join(fieldnames)}"
            )
        return column_map

    def _parse_date_returned(self, date_str):
        """
        Parse a DateReturned value into a datetime for sorting.

        The au3 version split on the first space (to drop a trailing time,
        e.g. "8/4/2026 14:30") and assumed M/D/YYYY. Same approach here,
        tried against a couple of format variants before giving up.
        """
        if not date_str:
            return None
        date_only = date_str.split(" ")[0].strip()
        for fmt in DATE_RETURNED_FORMATS:
            try:
                return datetime.strptime(date_only, fmt)
            except ValueError:
                continue
        return None

    def _ballot_sort_key(self, ballot_str):
        """
        Sort ballot numbers numerically when possible (so "10" sorts after
        "5", not before it -- see note in synthesize_table). Falls back to
        the raw string for any ballot number that isn't purely numeric, so
        this doesn't crash on unexpected formats like "A-12".
        """
        try:
            return (0, int(ballot_str))
        except ValueError:
            return (1, ballot_str)

    def _populate_table(self):
        # A freshly loaded CSV starts a new audit session -- any highlights
        # or counts from a previously loaded file shouldn't carry over.
        self.matched_indices = set()
        self.not_found_values = set()
        self.found_count = 0
        self.not_found_count = 0
        self.found_count_var.set("Found: 0")
        self.not_found_count_var.set("Not Found: 0")

        # Reset the Treeview's columns to the compact display set (not the
        # full CSV) -- click a row to see every field in a popup instead.
        self.tree.delete(*self.tree.get_children())
        display_labels = [label for label, _actual_key in self.display_columns]
        self.tree["columns"] = display_labels
        for label in display_labels:
            self.tree.heading(label, text=label)
            self.tree.column(label, width=120, anchor="w")

        self.row_id_by_index = {}
        for i, record in enumerate(self.records):
            values = [record.get(actual_key, "") for _label, actual_key in self.display_columns]
            # Treeview stores multiline strings on one visual line by
            # default; replace embedded newlines with a visible marker so
            # multiline quoted fields don't look broken in the grid.
            values = [v.replace("\n", " / ").replace("\r", "") for v in values]
            item_id = self.tree.insert("", "end", iid=str(i), values=values)
            self.row_id_by_index[i] = item_id

    def _rebuild_match_index(self):
        # VoterID is always the match column -- synthesize_table resolves
        # its actual header name/casing (e.g. "VOTERID") into
        # self.voter_id_column, since that varies per file.
        self.index_by_value = {}
        for i, record in enumerate(self.records):
            value = (record.get(self.voter_id_column) or "").strip()
            self.index_by_value[value] = i

        # Used by on_scan to flag a scan whose length doesn't match any
        # real VoterID as a likely misread, distinct from a genuine "not
        # found" (ballot for a different precinct, etc). Left empty (no
        # gating) if the loaded file's VoterIDs aren't a consistent
        # length -- some jurisdictions' IDs do vary, and we'd rather gate
        # on nothing than gate on a wrong assumption.
        lengths = {len(v) for v in self.index_by_value if v}
        self.voter_id_lengths = lengths

    # ------------------------------------------------------------------
    # Barcode scan handling
    # ------------------------------------------------------------------
    def _on_scan_keypress(self, event):
        """
        Gatekeeper for every keystroke that lands in the scan box, run
        before Tk's default Entry handling sees it. Three jobs:

        1. Log the raw keysym with a timestamp (see _flush_scan_log) so a
           bad scan can be diagnosed after the fact -- whether Tk received
           the wrong thing, or never received something at all.
        2. Normalize numeric-keypad keystrokes (KEYPAD_DIGIT_MAP) -- a
           keypad-emulating scanner can send navigation keysyms (KP_Up,
           KP_End, ...) instead of digits depending on Tk's (unreliable)
           tracking of NumLock state.
        3. Refuse to insert anything that isn't a digit or a recognized
           editing/modifier key. A VoterID barcode is digits only, so a
           letter or symbol reaching this point is never a legitimate
           scan -- it's a corrupted keystroke, and the safest thing to do
           with it is nothing, rather than let it silently become part of
           the "scanned" value.
        """
        keysym = event.keysym
        if SCAN_DEBUG_LOG_ENABLED:
            self._scan_keystroke_log.append((datetime.now(), keysym, event.char))

        digit = KEYPAD_DIGIT_MAP.get(keysym)
        if digit is not None:
            widget = event.widget
            try:
                if widget.selection_present():
                    widget.delete("sel.first", "sel.last")
            except tk.TclError:
                pass
            widget.insert("insert", digit)
            return "break"

        # Plain top-row/main-keyboard digit -- by far the common case for
        # most scanners -- let Tk's default (fast, C-level) handling
        # insert it; no need to do that work ourselves.
        if len(keysym) == 1 and keysym.isdigit():
            return None

        if keysym in ALLOWED_EDITING_KEYSYMS or keysym in MODIFIER_KEYSYMS:
            return None

        # Anything else -- letters, punctuation, function keys, etc. --
        # is refused outright rather than inserted.
        return "break"

    def _flush_scan_log(self, final_value):
        """
        Append this scan's raw keystroke timeline to SCAN_DEBUG_LOG_PATH
        and reset the in-memory buffer for the next scan. Done here
        (after Enter, off the critical timing path) rather than per
        keystroke, so logging itself can't be a contributor to dropped or
        delayed input during the actual scan burst.
        """
        if SCAN_DEBUG_LOG_ENABLED and self._scan_keystroke_log:
            try:
                with open(SCAN_DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
                    f.write(f"--- scan at {datetime.now().isoformat()} -> {final_value!r} ---\n")
                    prev_time = None
                    for keystroke_time, keysym, char in self._scan_keystroke_log:
                        delta_ms = (
                            "  0.0"
                            if prev_time is None
                            else f"{(keystroke_time - prev_time).total_seconds() * 1000:5.1f}"
                        )
                        f.write(f"  +{delta_ms}ms  keysym={keysym!r}  char={char!r}\n")
                        prev_time = keystroke_time
            except OSError:
                pass  # diagnostics are best-effort -- never block a real scan on this
        self._scan_keystroke_log = []

    def on_scan(self, event=None):
        scanned_value = self.scan_var.get().strip()
        self.scan_var.set("")  # clear the box for the next scan
        self._flush_scan_log(scanned_value)

        if not self.records:
            self.status_var.set("No CSV loaded yet.")
            return
        if not scanned_value:
            return

        # A length that matches no real VoterID at all is a strong signal
        # of a corrupted scan (a dropped or doubled character) rather than
        # a genuine not-found (e.g. ballot for a different precinct) --
        # tell the operator to rescan rather than lumping it in with real
        # mismatches. Purely a messaging improvement: a length that
        # doesn't exist can never match index_by_value anyway, so this
        # doesn't change *whether* a match is found, only how it's
        # explained.
        if self.voter_id_lengths and len(scanned_value) not in self.voter_id_lengths:
            self.status_var.set(
                f'"{scanned_value}" is {len(scanned_value)} digits -- expected '
                f"{'/'.join(str(n) for n in sorted(self.voter_id_lengths))}. "
                "Likely misread -- please rescan."
            )
            self._flash_scan_entry("#ffb3b3")
            messagebox.showwarning(
                "Possible Misread",
                f'Scanned code "{scanned_value}" is {len(scanned_value)} digits, '
                f"which doesn't match any VoterID length in this file "
                f"({'/'.join(str(n) for n in sorted(self.voter_id_lengths))}).\n\n"
                "This usually means the scan was corrupted partway through. "
                "Please rescan this ballot.",
            )
            return

        row_index = self.index_by_value.get(scanned_value)
        if row_index is None:
            # Only count each distinct unrecognized code once -- rescanning
            # the same bad code repeatedly (e.g. an accidental double-scan)
            # shouldn't inflate the count. Still warn every time though:
            # a repeat scan of a genuinely bad code is still worth
            # flagging to the operator each time it happens.
            if scanned_value not in self.not_found_values:
                self.not_found_values.add(scanned_value)
                self.not_found_count += 1
                self.not_found_count_var.set(f"Not Found: {self.not_found_count}")
            self.status_var.set(f'No match found for "{scanned_value}"')
            self._flash_scan_entry("#ffb3b3")  # light red flash
            self._show_not_found_popup(scanned_value)
            return

        # Every matched row stays highlighted -- this set is the source of
        # truth for which rows are highlighted, not the Treeview tags
        # themselves, so a full table rebuild (_populate_table) can
        # reapply it correctly instead of relying on tags surviving a
        # from-scratch redraw. It's also what makes the found count
        # unique-per-record: rescanning an already-matched barcode adds
        # nothing new to this set, so the count below only increments the
        # first time a given record is matched.
        newly_matched = row_index not in self.matched_indices
        self.matched_indices.add(row_index)
        if newly_matched:
            self.found_count += 1
            self.found_count_var.set(f"Found: {self.found_count}")

        item_id = self.row_id_by_index[row_index]
        self.tree.item(item_id, tags=("match",))
        self._scroll_to_keep_row_in_view(item_id)
        self.tree.selection_set(item_id)
        self.status_var.set(f'Match found for "{scanned_value}" (row {row_index + 1})')

        # Ballots are typically processed in bundles of 50 -- announce
        # every multiple (50, 100, 150...), not just the first time, so
        # this stays useful across an entire session, not just once.
        # Gated on newly_matched so rescanning an already-found ballot
        # can't accidentally re-trigger this.
        if newly_matched and self.found_count % BUNDLE_SIZE == 0:
            messagebox.showinfo(
                "Bundle Complete", f"{self.found_count} ballots scanned"
            )

    def _scroll_to_keep_row_in_view(self, item_id):
        """
        Scroll the table so the given row stays comfortably in view as
        more scans come in. tree.see() only scrolls the minimum amount
        needed to make a row visible at all, which tends to leave it
        pinned right at the bottom edge as you keep scanning down a
        sorted list -- this instead recenters the view whenever the row
        is out of sight entirely, or sitting in roughly the bottom
        quarter of the currently visible rows.
        """
        total_rows = len(self.tree.get_children())
        if total_rows == 0:
            return

        row_position = self.tree.index(item_id)
        row_fraction = row_position / total_rows

        first_visible, last_visible = self.tree.yview()
        visible_span = last_visible - first_visible
        if visible_span <= 0 or visible_span >= 1.0:
            return  # nothing to scroll -- no rows visible, or they all fit already

        position_in_view = (row_fraction - first_visible) / visible_span

        NEAR_BOTTOM_THRESHOLD = 0.75  # bottom quarter of the visible rows
        out_of_view = position_in_view < 0 or position_in_view > 1
        near_bottom = position_in_view >= NEAR_BOTTOM_THRESHOLD

        if not (out_of_view or near_bottom):
            return  # already comfortably visible -- leave the scroll alone

        target_first = row_fraction - (visible_span / 2)
        target_first = max(0.0, min(target_first, 1.0 - visible_span))
        self.tree.yview_moveto(target_first)

    def _show_not_found_popup(self, scanned_value):
        """
        A custom replacement for messagebox.showwarning(). The stock
        messagebox binds <Return> to its default button -- fine for a
        human, but a barcode scanner always ends its input with an Enter
        keystroke, so a scan sent *while this dialog is open* would
        instantly dismiss it (and be lost) before anyone could read it.

        This dialog avoids that entirely: nothing here has keyboard
        focus, <Return> isn't bound to anything, and it can only be
        closed by clicking "OK" with the mouse or pressing Escape --
        neither of which a scanner can trigger. A repeating beep runs
        until it's dismissed, so it's hard to miss even without looking
        at the screen.
        """
        popup = tk.Toplevel(self.root)
        popup.title("Code Not Found")
        popup.transient(self.root)
        popup.resizable(False, False)
        popup.configure(bg="#fdecea")

        frame = tk.Frame(popup, padx=28, pady=22, bg="#fdecea")
        frame.pack()
        tk.Label(frame, text="\u26A0", font=("", 36), fg="#c0392b", bg="#fdecea").pack()
        tk.Label(
            frame,
            text=f'"{scanned_value}" was not found in the loaded CSV.',
            font=("", 11), wraplength=340, justify="center", bg="#fdecea",
        ).pack(pady=(10, 4))
        tk.Label(
            frame, text="Click OK to continue scanning.",
            font=("", 9), fg="#6b7280", bg="#fdecea",
        ).pack(pady=(0, 16))

        # Deliberately NOT calling .focus_set() on this button and NOT
        # binding <Return> anywhere in this dialog -- see docstring above.
        close_btn = tk.Button(frame, text="OK", width=14, command=popup.destroy)
        close_btn.pack()
        popup.bind("<Escape>", lambda e: popup.destroy())

        # Repeating audible alert: beeps again every ~1.2s for as long as
        # the popup still exists, so it stops automatically once closed.
        # winsound.MessageBeep (Windows-only, this app's deployment target)
        # is more reliably audible than Tk's bell(), which depends on the
        # OS/desktop's system-bell settings -- often muted by default on
        # Linux dev machines even when it's technically working.
        def beep():
            if not popup.winfo_exists():
                return
            if winsound is not None:
                winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            else:
                self.root.bell()
            popup.after(1200, beep)
        beep()

        # Modal: blocks interaction with the main window, and grabs real
        # OS-level keyboard focus (not just Tk's internal notion of it) so
        # scanner input during this dialog lands here, not in the scan box.
        popup.grab_set()
        popup.focus_force()
        popup.wait_window()

        # Belt-and-suspenders: make sure the scan box is empty and
        # refocused once the operator has acknowledged the warning, in
        # case anything leaked through while the dialog was up.
        self.scan_var.set("")
        self.scan_entry.focus_set()

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

    # ------------------------------------------------------------------
    # Row details popup (click a row -> see every original CSV field)
    # ------------------------------------------------------------------
    def _on_row_click(self, event):
        # identify_row returns '' for clicks on the header or empty area
        # below the last row -- ignore those, only real data rows expand.
        item_id = self.tree.identify_row(event.y)
        if not item_id:
            return
        try:
            row_index = int(item_id)
        except ValueError:
            return
        self._show_record_details(row_index)
        # Reclaim focus immediately rather than waiting up to 500ms for
        # the periodic _refocus_scan_entry cycle -- this popup is
        # non-modal (the user might leave it open while continuing to
        # scan), so the scan box should keep receiving keystrokes right
        # away, not just eventually.
        self.scan_entry.focus_set()

    def _show_record_details(self, row_index):
        record = self.records[row_index]

        popup = tk.Toplevel(self.root)
        popup.title("Record Details")
        popup.transient(self.root)  # stays on top of / minimizes with the main window

        container = tk.Frame(popup, padx=16, pady=12)
        container.pack(fill=tk.BOTH, expand=True)

        # self.fieldnames is the *full* original CSV header list (unlike
        # self.display_columns, the compact subset shown in the table),
        # so this shows every column regardless of what's in the grid.
        for row, key in enumerate(self.fieldnames):
            value = (record.get(key) or "").replace("\r", "")
            tk.Label(
                container, text=f"{key}:", font=("", 9, "bold"), anchor="ne", justify="right"
            ).grid(row=row, column=0, sticky="ne", padx=(0, 10), pady=2)
            tk.Label(
                container, text=value, anchor="nw", justify="left", wraplength=420
            ).grid(row=row, column=1, sticky="nw", pady=2)

        close_btn = tk.Button(popup, text="Close", command=popup.destroy)
        close_btn.pack(pady=(0, 12))

        popup.bind("<Escape>", lambda e: popup.destroy())


def main():
    root = tk.Tk()
    app = BallotAuditor(root)

    # Allow "python barcode_lookup.py mydata.csv" to open a file on launch
    if len(sys.argv) > 1:
        app.load_csv(sys.argv[1])

    root.mainloop()


if __name__ == "__main__":
    main()
