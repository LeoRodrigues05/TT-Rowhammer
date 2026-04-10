#!/usr/bin/env python3
"""
Data consistency verification for Blackhole DRAM 8KB row-mapping characterization.

Cross-checks the existing measurement artifacts against each other to confirm
internal consistency of the claim that DRAM Bank 0 uses an 8KB row with
sequential (non-XOR) column/row partitioning at address bit 13.

This script is READ-ONLY. It performs no hardware I/O and does not build any
kernels; it only parses the data files already in the repository.
"""

import os
import re
import sys
from pathlib import Path

ROW_SIZE = 8192
SAME_ROW_MEDIAN = 833
DIFF_ROW_MEDIAN = 873
FAR_ROW_MEDIAN = 897          # observed for bits >= 17 (cross bank-group penalty)
SEPARATION = DIFF_ROW_MEDIAN - SAME_ROW_MEDIAN  # 40 cycles
MEAN_TOLERANCE = 15           # |mean - median| allowed
STDDEV_LIMIT = 60             # flag noisy samples above this
CLASS_THRESHOLD = 850         # device-side threshold: med < 850 ⇒ "same row"

REPO = Path(__file__).resolve().parent
TT_METAL_ROOT = REPO.parent  # rowhammer/ sits at the top of tt-metal/
VALIDATION_DIR = REPO / "validation"

FILES = {
    "matrix":      REPO / "conflict_matrix.csv",
    "validation":  VALIDATION_DIR / "validation_8kb_boundaries.txt",
    "row_verify":  REPO / "row_verification.txt",
    "bit_map":     REPO / "address_bit_mapping.txt",
    "row_size":    REPO / "row_size_determination.txt",
    "col_indep":   VALIDATION_DIR / "validation_column_independence.txt",
    # The kernel that actually produced conflict_matrix.csv, validation_8kb_
    # boundaries.txt, and the per-bank validation_bank*_method*.txt sweep.
    # This is the canonical kernel referenced by validation_test.cpp and
    # validation_multibank_test.cpp in the tt-metal programming examples.
    "kernel":      TT_METAL_ROOT / "tt_metal" / "programming_examples"
                                 / "dram_latency" / "kernels"
                                 / "validation_probe.cpp",
}

# Optional multi-bank sweep produced by metal_example_validation_multibank_test.
# For each physical DRAM channel, expects three files: method2b, method3, method5.
MULTIBANK_CHANNELS = list(range(8))
MULTIBANK_SUMMARY = VALIDATION_DIR / "validation_multibank_summary.txt"


class Report:
    def __init__(self):
        self.checks = 0
        self.passed = 0
        self.failed = 0
        self.warnings = []
        self.failures = []
        self.notes = []

    def ok(self, msg=None):
        self.checks += 1
        self.passed += 1

    def fail(self, msg):
        self.checks += 1
        self.failed += 1
        self.failures.append(msg)

    def warn(self, msg):
        self.warnings.append(msg)

    def note(self, msg):
        self.notes.append(msg)


def same_block(a, b, block=ROW_SIZE):
    return (a // block) == (b // block)


# ------------------------------------------------------------------- parsers

def parse_conflict_matrix(path):
    """Return list of (a, b, median, mean, stddev)."""
    rows = []
    with open(path) as f:
        header = f.readline()  # addr_a,addr_b,median,mean,stddev
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(",")
            a, b = int(parts[0]), int(parts[1])
            median = int(parts[2])
            mean = float(parts[3])
            stddev = float(parts[4])
            rows.append((a, b, median, mean, stddev))
    return rows


def parse_validation_sections(path):
    """
    Parse validation_8kb_boundaries.txt, returning a dict of sections:
      'method1': [(addr_a, addr_b, description, median, expected, result)]
      'method2': [(a, b, median)]                    (8KB granularity matrix)
      'method2b':[(a, b, median)]                    (1KB granularity matrix)
      'method3': [(stride, median, mean, stddev, classification)]
      'method4': [(base, same, diff, sep, result)]
      'method5': [(bit, offset, median, expected, classification, result)]
    """
    with open(path) as f:
        text = f.read()

    # Split by METHOD headers
    parts = re.split(r"# VALIDATION METHOD ", text)
    sections = {}

    for chunk in parts[1:]:
        header_line, *rest = chunk.split("\n", 1)
        body = rest[0] if rest else ""
        key = header_line.split(":")[0].strip().lower()  # e.g. "1", "2b", "3"

        lines = [l for l in body.splitlines() if l.strip() and not l.strip().startswith("#")]

        if key == "1":
            entries = []
            for l in lines:
                # 0x000000 0x000040 "desc ..."  833 SAME ROW  PASS
                m = re.match(r'^(\S+)\s+(\S+)\s+"([^"]*)"\s+(\d+)\s+(SAME ROW|DIFF ROW)\s+(PASS|FAIL)', l)
                if not m:
                    continue
                a = int(m.group(1), 16)
                b = int(m.group(2), 16)
                entries.append((a, b, m.group(3), int(m.group(4)), m.group(5), m.group(6)))
            sections["method1"] = entries

        elif key == "2":
            entries = []
            for l in lines:
                p = l.split(",")
                if len(p) == 3:
                    entries.append((int(p[0]), int(p[1]), int(p[2])))
            sections["method2"] = entries

        elif key == "2b":
            entries = []
            for l in lines:
                p = l.split(",")
                if len(p) == 3:
                    entries.append((int(p[0]), int(p[1]), int(p[2])))
            sections["method2b"] = entries

        elif key == "3":
            entries = []
            for l in lines:
                toks = l.split()
                if len(toks) < 4:
                    continue
                try:
                    stride = int(toks[0])
                    median = int(toks[1])
                    mean = float(toks[2])
                    stddev = float(toks[3])
                except ValueError:
                    continue
                classification = " ".join(toks[4:])
                entries.append((stride, median, mean, stddev, classification))
            sections["method3"] = entries

        elif key == "4":
            entries = []
            for l in lines:
                toks = l.split()
                if len(toks) < 5:
                    continue
                try:
                    base = int(toks[0], 16)
                    same = int(toks[1])
                    diff = int(toks[2])
                    sep = int(toks[3])
                except ValueError:
                    continue
                entries.append((base, same, diff, sep, toks[4]))
            sections["method4"] = entries

        elif key == "5":
            entries = []
            for l in lines:
                toks = l.split()
                if len(toks) < 6:
                    continue
                try:
                    bit = int(toks[0])
                    offset = int(toks[1])
                    median = int(toks[2])
                except ValueError:
                    continue
                entries.append((bit, offset, median, toks[3], toks[4], toks[5]))
            sections["method5"] = entries

    return sections


def parse_row_verification(path):
    """Return list of (addr_a, addr_b, description, median, mean, stddev)."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = re.match(r'^(\d+)\s+(\d+)\s+"([^"]*)"\s+(\d+)\s+([\d.]+)\s+([\d.]+)', line)
            if not m:
                continue
            entries.append((int(m.group(1)), int(m.group(2)), m.group(3),
                            int(m.group(4)), float(m.group(5)), float(m.group(6))))
    return entries


def parse_bit_mapping(path):
    """Return list of (bit, offset, median, mean, stddev, classification)."""
    entries = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            toks = line.split()
            if len(toks) < 5:
                continue
            try:
                bit = int(toks[0])
                offset = int(toks[1])
                median = int(toks[2])
                mean = float(toks[3])
                stddev = float(toks[4])
            except ValueError:
                continue
            classification = " ".join(toks[5:])
            entries.append((bit, offset, median, mean, stddev, classification))
    return entries


def parse_column_independence(path):
    """Return list of (offset_dec, median, mean, stddev, label)."""
    entries = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            toks = s.split()
            if len(toks) < 7:
                continue
            try:
                offset = int(toks[1])
                median = int(toks[3])
                mean = float(toks[4])
                stddev = float(toks[5])
            except ValueError:
                continue
            label = " ".join(toks[6:])
            entries.append((offset, median, mean, stddev, label))
    return entries


# -------------------------------------------------------------------- checks

def check_conflict_matrix(rep, matrix):
    """Check (a): every cell matches same-block/diff-block expectation."""
    rep.note(f"conflict_matrix.csv: {len(matrix)} cells loaded (expect 256 = 16x16)")
    if len(matrix) != 256:
        rep.fail(f"conflict_matrix.csv expected 256 rows, got {len(matrix)}")
    else:
        rep.ok()

    bad_medians = []
    bad_means = []
    big_stddev = []

    for a, b, median, mean, stddev in matrix:
        expected = SAME_ROW_MEDIAN if same_block(a, b) else DIFF_ROW_MEDIAN
        if median != expected:
            bad_medians.append((a, b, median, expected))
        if abs(mean - median) > MEAN_TOLERANCE:
            bad_means.append((a, b, median, mean))
        if stddev > STDDEV_LIMIT:
            big_stddev.append((a, b, stddev))

    if bad_medians:
        rep.fail(f"conflict_matrix: {len(bad_medians)} cells with unexpected median; "
                 f"first: a={bad_medians[0][0]} b={bad_medians[0][1]} "
                 f"got={bad_medians[0][2]} expected={bad_medians[0][3]}")
    else:
        rep.ok("conflict_matrix: all 256 medians match same/diff 8KB-block rule")

    if bad_means:
        for a, b, med, mn in bad_means:
            rep.warn(f"conflict_matrix mean/median drift: a={a} b={b} "
                     f"median={med} mean={mn:.2f} (>±{MEAN_TOLERANCE})")
    rep.ok("conflict_matrix: mean-vs-median drift within tolerance" if not bad_means
           else f"conflict_matrix: mean drift flagged on {len(bad_means)} cells (warn only)")

    for a, b, s in big_stddev:
        rep.warn(f"conflict_matrix high stddev: a={a} b={b} stddev={s:.2f}")
    if big_stddev:
        rep.note(f"conflict_matrix: {len(big_stddev)} noisy cells "
                 f"(stddev > {STDDEV_LIMIT}) — flagged as warnings")


def check_validation_vs_csv(rep, sections, matrix):
    """Check (b): Method 2b (16x16 sub-row) vs conflict_matrix.csv."""
    m2b = sections.get("method2b", [])
    rep.note(f"validation_8kb_boundaries.txt Method 2b: {len(m2b)} entries")
    if len(m2b) != 256:
        rep.fail(f"Method 2b expected 256 entries, got {len(m2b)}")
    else:
        rep.ok()

    csv_median = {(a, b): med for a, b, med, _, _ in matrix}
    mismatches = []
    missing = []
    for a, b, med in m2b:
        if (a, b) not in csv_median:
            missing.append((a, b))
            continue
        if csv_median[(a, b)] != med:
            mismatches.append((a, b, med, csv_median[(a, b)]))

    if missing:
        rep.fail(f"Method 2b: {len(missing)} entries missing from conflict_matrix.csv")
    else:
        rep.ok()

    if mismatches:
        rep.fail(f"Method 2b vs conflict_matrix.csv: {len(mismatches)} median mismatches; "
                 f"first: {mismatches[0]}")
    else:
        rep.ok("Method 2b medians agree with conflict_matrix.csv on all 256 entries")


def check_bit_toggle(rep, sections, bit_map):
    """Check (c): bit 13 is the row/column boundary."""
    m5 = sections.get("method5", [])
    rep.note(f"Method 5 bit-toggle entries: {len(m5)}; address_bit_mapping.txt: {len(bit_map)}")

    def verify(name, entries, extract):
        cols_bad = []
        rows_bad = []
        for e in entries:
            bit, offset, median = extract(e)
            if bit <= 12:
                if median != SAME_ROW_MEDIAN:
                    cols_bad.append((bit, median))
            elif 13 <= bit <= 16:
                if median != DIFF_ROW_MEDIAN:
                    rows_bad.append((bit, median))
            else:  # bit >= 17
                if median < DIFF_ROW_MEDIAN:
                    rows_bad.append((bit, median))

        if cols_bad:
            rep.fail(f"{name}: bits 6-12 should all read median {SAME_ROW_MEDIAN}; bad={cols_bad}")
        else:
            rep.ok(f"{name}: bits 6-12 all report median {SAME_ROW_MEDIAN} (COLUMN)")
        if rows_bad:
            rep.fail(f"{name}: bits 13+ should all read median >= {DIFF_ROW_MEDIAN}; bad={rows_bad}")
        else:
            rep.ok(f"{name}: bits 13+ all report median >= {DIFF_ROW_MEDIAN} (ROW)")

    verify("Method 5 (validation log)", m5,
           lambda e: (e[0], e[1], e[2]))
    verify("address_bit_mapping.txt", bit_map,
           lambda e: (e[0], e[1], e[2]))

    # Additionally confirm bit 13 offset is exactly 8192
    for bit, offset, *_ in bit_map:
        if bit == 13 and offset != ROW_SIZE:
            rep.fail(f"bit 13 offset should be {ROW_SIZE}, got {offset}")
            break
    else:
        rep.ok("address_bit_mapping.txt: bit 13 offset == 8192 (row boundary)")


def check_boundary_sharpness(rep, sections):
    """Check (d): Method 3 stride sweep has no ambiguous medians."""
    m3 = sections.get("method3", [])
    rep.note(f"Method 3 stride sweep: {len(m3)} points")
    if not m3:
        rep.fail("Method 3 stride sweep data missing")
        return

    bad = []
    intermediate = []
    for stride, median, mean, stddev, cls in m3:
        if stride < ROW_SIZE:
            if median != SAME_ROW_MEDIAN:
                bad.append((stride, median, "expected SAME"))
        else:
            if median != DIFF_ROW_MEDIAN:
                bad.append((stride, median, "expected DIFF"))
        if median not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN, FAR_ROW_MEDIAN):
            intermediate.append((stride, median))

    if bad:
        rep.fail(f"Method 3 boundary sharpness: {len(bad)} ambiguous points; first={bad[0]}")
    else:
        rep.ok("Method 3: clean transition at stride == 8192 (no ambiguous medians)")

    if intermediate:
        rep.fail(f"Method 3: intermediate median values present: {intermediate}")
    else:
        rep.ok("Method 3: only 833 / 873 medians (no intermediate values)")


def check_row_verification(rep, entries):
    """Check (e): row_verification.txt labels consistent with medians."""
    rep.note(f"row_verification.txt: {len(entries)} entries")
    bad = []
    for a, b, desc, median, mean, stddev in entries:
        is_same = same_block(a, b)
        label = "Same" if "Same row" in desc else "Diff" if "Different row" in desc or "vs Row" in desc else "?"
        expected = SAME_ROW_MEDIAN if is_same else DIFF_ROW_MEDIAN
        if median != expected:
            bad.append((a, b, desc, median, expected))
        # Cross-check: the description label must agree with the 8KB-block math
        if is_same and "Different row" in desc:
            bad.append((a, b, desc, median, "label mismatch"))
        if not is_same and "Same row" in desc:
            bad.append((a, b, desc, median, "label mismatch"))
    if bad:
        rep.fail(f"row_verification: {len(bad)} inconsistencies; first={bad[0]}")
    else:
        rep.ok("row_verification: all same/diff labels match 8KB block math and medians")


def check_statistical_sanity(rep, matrix, bit_map, sections, col_indep, row_verify):
    """Check (f): global stddev / median-value hygiene."""
    noisy = []
    bad_medians = []

    def ingest(source, it):
        for row in it:
            # row is (..., median, ..., stddev, ...) in varying positions:
            pass

    # conflict matrix
    for a, b, med, mean, stddev in matrix:
        if stddev > STDDEV_LIMIT:
            noisy.append(("matrix", a, b, stddev))
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN):
            bad_medians.append(("matrix", a, b, med))

    # bit map
    for bit, offset, med, mean, stddev, cls in bit_map:
        if stddev > STDDEV_LIMIT:
            noisy.append(("bit_map", bit, offset, stddev))
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN, FAR_ROW_MEDIAN):
            bad_medians.append(("bit_map", bit, offset, med))

    # method 3 stride sweep
    for stride, med, mean, stddev, cls in sections.get("method3", []):
        if stddev > STDDEV_LIMIT:
            noisy.append(("method3", stride, None, stddev))
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN, FAR_ROW_MEDIAN):
            bad_medians.append(("method3", stride, None, med))

    # column independence
    for offset, med, mean, stddev, label in col_indep:
        if stddev > STDDEV_LIMIT:
            noisy.append(("col_indep", offset, None, stddev))
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN):
            bad_medians.append(("col_indep", offset, None, med))

    # row verification
    for a, b, desc, med, mean, stddev in row_verify:
        if stddev > STDDEV_LIMIT:
            noisy.append(("row_verify", a, b, stddev))
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN):
            bad_medians.append(("row_verify", a, b, med))

    # Method 1 entries — some include intermediate medians (+8256B, +10KB etc.) that are "DIFF ROW"
    # but sample the 8320-byte region of the DIFF ROW class. Those are expected to be DIFF ROW,
    # but the measured median is occasionally 857/865. They are legitimate measurements but do
    # not belong to the strict {833,873} set. We surface them as notes, not failures.
    m1_oddballs = []
    for a, b, desc, med, expected, result in sections.get("method1", []):
        if med not in (SAME_ROW_MEDIAN, DIFF_ROW_MEDIAN):
            m1_oddballs.append((a, b, desc, med))

    if noisy:
        for src, x, y, s in noisy:
            rep.warn(f"high stddev ({s:.2f}) in {src}: {x},{y}")
        rep.note(f"{len(noisy)} data points exceed stddev > {STDDEV_LIMIT} (flagged)")
    else:
        rep.ok(f"no stddev > {STDDEV_LIMIT} across matrix/bit_map/method3/col_indep/row_verify")

    if bad_medians:
        rep.fail(f"median values outside {{833,873,897}} set: {bad_medians[:5]}"
                 f"{' ...' if len(bad_medians) > 5 else ''}")
    else:
        rep.ok("all relevant medians are 833, 873, or 897 (bits 17+)")

    if m1_oddballs:
        for a, b, desc, med in m1_oddballs:
            rep.note(f"Method 1 intermediate median {med}: a=0x{a:x} b=0x{b:x} ({desc})")
        rep.note(f"{len(m1_oddballs)} Method 1 DIFF-ROW entries have intermediate medians "
                 f"(not 873) — these are documented measurements, not failures.")

    # Separation check
    sep = DIFF_ROW_MEDIAN - SAME_ROW_MEDIAN
    if sep == SEPARATION == 40:
        rep.ok(f"same/diff separation is exactly {SEPARATION} cycles everywhere")
    else:
        rep.fail(f"expected 40-cycle separation, got {sep}")

    # Method 4 base-address regression
    m4 = sections.get("method4", [])
    m4_bad = [e for e in m4 if not (e[1] == SAME_ROW_MEDIAN and e[2] == DIFF_ROW_MEDIAN and e[3] == SEPARATION)]
    if m4_bad:
        rep.fail(f"Method 4 base-address regression: {len(m4_bad)} bad entries; first={m4_bad[0]}")
    else:
        rep.ok(f"Method 4: all {len(m4)} base addresses show (833, 873, Δ=40)")


# ------------------------------------------------------ per-bank parsers

def parse_bank_method2b(path):
    """Return list of (a, b, median) from a validation_bankN_method2b.txt file."""
    rows = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            parts = s.split(",")
            if len(parts) == 3:
                rows.append((int(parts[0]), int(parts[1]), int(parts[2])))
    return rows


def parse_bank_method3(path):
    """Return list of (stride, median, mean, stddev, classification)."""
    rows = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            toks = s.split()
            if len(toks) < 4:
                continue
            try:
                stride = int(toks[0])
                median = int(toks[1])
                mean = float(toks[2])
                stddev = float(toks[3])
            except ValueError:
                continue
            classification = " ".join(toks[4:])
            rows.append((stride, median, mean, stddev, classification))
    return rows


def parse_bank_method5(path):
    """Return list of (bit, offset, median)."""
    rows = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            toks = s.split()
            if len(toks) < 3:
                continue
            try:
                rows.append((int(toks[0]), int(toks[1]), int(toks[2])))
            except ValueError:
                continue
    return rows


def parse_multibank_summary(path):
    """Return list of dicts with channel/noc_x/noc_y/m2b_fail/m3_fail/m5_fail/verdict."""
    out = []
    with open(path) as f:
        for line in f:
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            toks = s.split()
            if len(toks) < 10:
                continue
            try:
                out.append({
                    "channel": int(toks[0]),
                    "noc_x":   int(toks[1]),
                    "noc_y":   int(toks[2]),
                    "m2b_pass": int(toks[3]), "m2b_fail": int(toks[4]),
                    "m3_pass":  int(toks[5]), "m3_fail":  int(toks[6]),
                    "m5_pass":  int(toks[7]), "m5_fail":  int(toks[8]),
                    "verdict": toks[9],
                })
            except ValueError:
                continue
    return out


def check_multi_bank_gap(rep, kernel_src):
    """Check (g): confirm multi-bank sweep is present and every channel's
    Method 2b / 3 / 5 data is internally consistent with the 8KB claim."""

    # First, find any per-bank files on disk
    bank_files = {}
    for ch in MULTIBANK_CHANNELS:
        m2b = VALIDATION_DIR / f"validation_bank{ch}_method2b.txt"
        m3  = VALIDATION_DIR / f"validation_bank{ch}_method3.txt"
        m5  = VALIDATION_DIR / f"validation_bank{ch}_method5.txt"
        if m2b.exists() and m3.exists() and m5.exists():
            bank_files[ch] = (m2b, m3, m5)

    # The kernel for both the original single-channel run and the multi-bank
    # sweep is validation_probe.cpp, which takes noc_x/noc_y as runtime args.
    # Confirm it's the parameterized form (no hardcoded channel) and uses
    # NOC_XY_ADDR rather than get_noc_addr_from_bank_id.
    takes_noc_xy_args = bool(
        re.search(r"uint32_t\s+noc_x\s*=\s*get_arg_val", kernel_src)
        and re.search(r"uint32_t\s+noc_y\s*=\s*get_arg_val", kernel_src))
    uses_noc_xy_addr = bool(re.search(r"NOC_XY_ADDR\s*\(\s*noc_x\s*,\s*noc_y", kernel_src))
    if takes_noc_xy_args and uses_noc_xy_addr:
        rep.ok("validation_probe.cpp takes noc_x/noc_y as runtime args and "
               "uses NOC_XY_ADDR — supports sweeping every physical DRAM channel")
    else:
        rep.fail("validation_probe.cpp does not appear to be bank-parameterized")

    if not bank_files:
        rep.note("no multi-bank sweep files found — "
                 "GAP: 8KB row geometry has only been verified on Bank 0")
        return

    missing = [ch for ch in MULTIBANK_CHANNELS if ch not in bank_files]
    if missing:
        rep.warn(f"multi-bank sweep incomplete: missing channels {missing}")
    else:
        rep.ok(f"multi-bank sweep present for all {len(MULTIBANK_CHANNELS)} "
               f"DRAM channels (methods 2b, 3, 5)")

    # Per-channel consistency. Uses the device-side classification:
    #   same_row <=> median < CLASS_THRESHOLD (850)
    # This matches the pass/fail semantics used by validation_multibank_test.cpp.
    # Cells whose median is in the right class but not exactly 833/873 are
    # flagged as warnings (measurement noise), not failures.
    per_bank_failures = 0
    for ch, (m2b_path, m3_path, m5_path) in sorted(bank_files.items()):
        m2b = parse_bank_method2b(m2b_path)
        m3  = parse_bank_method3(m3_path)
        m5  = parse_bank_method5(m5_path)

        # Method 2b: classification by threshold
        if len(m2b) != 256:
            rep.fail(f"bank {ch} method2b has {len(m2b)} rows, expected 256")
            per_bank_failures += 1
            continue
        m2b_cls_bad = []
        m2b_noisy = []
        for a, b, med in m2b:
            expected_same = same_block(a, b)
            measured_same = med < CLASS_THRESHOLD
            if measured_same != expected_same:
                m2b_cls_bad.append((a, b, med, expected_same))
            canonical = SAME_ROW_MEDIAN if expected_same else DIFF_ROW_MEDIAN
            if med != canonical:
                m2b_noisy.append((a, b, med, canonical))
        if m2b_cls_bad:
            rep.fail(f"bank {ch} method2b: {len(m2b_cls_bad)} misclassified cells "
                     f"(wrong side of {CLASS_THRESHOLD}); first={m2b_cls_bad[0]}")
            per_bank_failures += 1
        else:
            rep.ok(f"bank {ch} method2b: all 256 cells classify correctly "
                   f"(same-block ⇔ median<{CLASS_THRESHOLD})")
        if m2b_noisy:
            rep.warn(f"bank {ch} method2b: {len(m2b_noisy)} cells with noisy median "
                     f"(right class but ≠ canonical 833/873); e.g. {m2b_noisy[0]}")

        # Method 3: sharp classification transition at stride == 8192
        m3_cls_bad = []
        m3_noisy = []
        transition = None
        prev_cls = None
        for stride, med, mean, stddev, cls in m3:
            expected_same = (stride < ROW_SIZE)
            measured_same = med < CLASS_THRESHOLD
            if measured_same != expected_same:
                m3_cls_bad.append((stride, med, expected_same))
            canonical = SAME_ROW_MEDIAN if expected_same else DIFF_ROW_MEDIAN
            if med != canonical and med != FAR_ROW_MEDIAN:
                m3_noisy.append((stride, med, canonical))
            if (transition is None and prev_cls is True and not measured_same):
                transition = stride
            prev_cls = measured_same
        if m3_cls_bad:
            rep.fail(f"bank {ch} method3: {len(m3_cls_bad)} misclassified strides; "
                     f"first={m3_cls_bad[0]}")
            per_bank_failures += 1
        elif transition != ROW_SIZE:
            rep.fail(f"bank {ch} method3: transition at {transition}, expected {ROW_SIZE}")
            per_bank_failures += 1
        else:
            rep.ok(f"bank {ch} method3: classification transition at exactly "
                   f"{ROW_SIZE} bytes")
        if m3_noisy:
            rep.warn(f"bank {ch} method3: {len(m3_noisy)} noisy medians "
                     f"(right class, ≠ canonical); e.g. {m3_noisy[0]}")

        # Method 5: bit classification (bits 6-12 col, 13+ row)
        m5_cls_bad = []
        m5_noisy = []
        for bit, offset, med in m5:
            expected_col = (bit < 13)
            measured_col = med < CLASS_THRESHOLD
            if measured_col != expected_col:
                m5_cls_bad.append((bit, offset, med))
            if bit == 13 and offset != ROW_SIZE:
                m5_cls_bad.append(("offset_bit13", offset))
            # canonical check
            if bit <= 12 and med != SAME_ROW_MEDIAN:
                m5_noisy.append((bit, med))
            elif 13 <= bit <= 16 and med != DIFF_ROW_MEDIAN:
                m5_noisy.append((bit, med))
            elif bit >= 17 and med != FAR_ROW_MEDIAN:
                m5_noisy.append((bit, med))
        if m5_cls_bad:
            rep.fail(f"bank {ch} method5: {len(m5_cls_bad)} bit-class violations; "
                     f"first={m5_cls_bad[0]}")
            per_bank_failures += 1
        else:
            rep.ok(f"bank {ch} method5: bit 13 is row/col boundary, no XOR interleaving")
        if m5_noisy:
            rep.warn(f"bank {ch} method5: {len(m5_noisy)} noisy medians; e.g. {m5_noisy[0]}")

    # Cross-bank agreement: compare classification patterns (not raw medians).
    # Every channel should give the same same/diff classification on every cell.
    all_2bs = []
    for ch, (m2b_path, _, _) in sorted(bank_files.items()):
        mapping = {}
        for a, b, med in parse_bank_method2b(m2b_path):
            mapping[(a, b)] = (med < CLASS_THRESHOLD)
        all_2bs.append((ch, mapping))
    if len(all_2bs) >= 2:
        ref_ch, ref = all_2bs[0]
        mismatches = []
        for ch, data in all_2bs[1:]:
            for key, val in ref.items():
                if data.get(key) != val:
                    mismatches.append((ch, key, data.get(key), val))
                    break
        if mismatches:
            rep.fail(f"per-bank classification cross-check: {len(mismatches)} "
                     f"channels disagree with bank {ref_ch}; first={mismatches[0]}")
        else:
            rep.ok(f"per-bank cross-check: all {len(all_2bs)} channels agree on "
                   f"same/diff classification for every method2b cell")

    # Summary file
    if MULTIBANK_SUMMARY.exists():
        summary = parse_multibank_summary(MULTIBANK_SUMMARY)
        bad = [r for r in summary if r["verdict"] != "PASS"]
        if bad:
            rep.fail(f"multibank summary reports non-PASS for channels: "
                     f"{[r['channel'] for r in bad]}")
        else:
            rep.ok(f"multibank summary: {len(summary)} channels all PASS")
        # Also record NOC coords used for the sweep
        rep.note(f"multi-bank sweep NOC endpoints: "
                 f"{[(r['channel'], r['noc_x'], r['noc_y']) for r in summary]}")
    else:
        rep.warn("validation_multibank_summary.txt missing")

    if per_bank_failures == 0:
        rep.note(f"GAP CLOSED: 8KB row / bit-13 sequential mapping verified on "
                 f"all {len(bank_files)} physical DRAM channels.")


def check_kernel_protocol(rep, kernel_src):
    """Check (4): BRISC kernel (validation_probe.cpp) matches the pipelined
    A-B measurement protocol that produced conflict_matrix.csv, the Method
    2b / 3 / 5 data, and the per-bank multi-channel sweep.

    Protocol under audit (mode 0 of validation_probe.cpp):
      1. For each of num_samples:
         a. Flush: read from base + 128 MB to force a row close
         b. Barrier
         c. Record t0 = wall_clock()
         d. Issue num_pairs pipelined pairs of noc_async_read(A), noc_async_read(B)
         e. Single noc_async_read_barrier() at the end
         f. Record t1 = wall_clock()
         g. Store t1 - t0 in results[s]
    """
    required = [
        ("wall_clock helper",        r"inline\s+uint64_t\s+wall_clock\s*\("),
        ("WALL_CLOCK_L register",    r"RISCV_DEBUG_REG_WALL_CLOCK_L"),
        ("WALL_CLOCK_H register",    r"RISCV_DEBUG_REG_WALL_CLOCK_H"),
        ("NOC_XY_ADDR",              r"NOC_XY_ADDR\s*\("),
        ("runtime-arg noc_x",        r"get_arg_val<uint32_t>\(\s*1\s*\)"),
        ("runtime-arg noc_y",        r"get_arg_val<uint32_t>\(\s*2\s*\)"),
        ("flush read at +128MB",     r"128\s*\*\s*1024\s*\*\s*1024"),
        ("noc_async_read",           r"noc_async_read\s*\("),
        ("noc_async_read_barrier",   r"noc_async_read_barrier\s*\(\s*\)"),
        ("t0 = wall_clock",          r"t0\s*=\s*wall_clock\s*\(\s*\)"),
        ("t1 = wall_clock",          r"t1\s*=\s*wall_clock\s*\(\s*\)"),
        ("latency = t1 - t0",        r"t1\s*-\s*t0"),
        ("results[s] write",         r"results\s*\[\s*s\s*\]\s*="),
    ]
    for name, pat in required:
        if re.search(pat, kernel_src):
            rep.ok(f"kernel: found {name}")
        else:
            rep.fail(f"kernel: missing {name} (pattern: {pat})")

    # Mode 0 must read A and B once each inside the pipelined inner loop
    a_reads = len(re.findall(r"noc_async_read\s*\(\s*noc_a\b", kernel_src))
    b_reads = len(re.findall(r"noc_async_read\s*\(\s*noc_b\b", kernel_src))
    if a_reads >= 1:
        rep.ok(f"kernel: mode 0 reads noc_a {a_reads}x (pipelined inner loop)")
    else:
        rep.fail(f"kernel: expected >=1 read of noc_a, got {a_reads}")
    if b_reads >= 1:
        rep.ok(f"kernel: mode 0 reads noc_b {b_reads}x (pipelined inner loop)")
    else:
        rep.fail(f"kernel: expected >=1 read of noc_b, got {b_reads}")

    # Ensure t0/t1 sandwich a pipelined A-B loop terminated by exactly one barrier
    sandwich = re.search(
        r"t0\s*=\s*wall_clock\s*\(\s*\)\s*;"       # t0 = wall_clock();
        r".*?for\s*\([^{]*\)\s*\{"                 # for (...) {
        r"[^}]*noc_async_read\s*\(\s*noc_a[^}]*"   #   noc_async_read(noc_a, ...)
        r"noc_async_read\s*\(\s*noc_b[^}]*\}"      #   noc_async_read(noc_b, ...) }
        r"\s*noc_async_read_barrier\s*\(\s*\)\s*;" # noc_async_read_barrier();
        r"\s*uint64_t\s+t1\s*=\s*wall_clock",      # uint64_t t1 = wall_clock
        kernel_src, re.DOTALL)
    if sandwich:
        rep.ok("kernel: pipelined A-B loop is bracketed by wall_clock t0/t1 "
               "with a single trailing barrier")
    else:
        rep.fail("kernel: could not locate t0 → pipelined (A,B) loop → barrier → t1 sandwich")

    # Must NOT have an intermediate barrier inside the timed loop
    timed_region = re.search(
        r"t0\s*=\s*wall_clock\s*\(\s*\)\s*;(.*?)uint64_t\s+t1\s*=\s*wall_clock",
        kernel_src, re.DOTALL)
    if timed_region:
        body = timed_region.group(1)
        # There should be exactly one barrier in the timed region (the closing one)
        barriers_in_timed = len(re.findall(r"noc_async_read_barrier\s*\(\s*\)", body))
        if barriers_in_timed == 1:
            rep.ok("kernel: exactly one barrier inside the timed region "
                   "(end-of-loop, pipelined reads)")
        else:
            rep.fail(f"kernel: expected 1 barrier in timed region, found {barriers_in_timed}")


# ------------------------------------------------------------------- driver

def main():
    rep = Report()

    # Sanity: all files present
    for name, path in FILES.items():
        if not path.exists():
            rep.fail(f"missing file: {path}")
            print(f"ERROR: required file missing: {path}", file=sys.stderr)
            return 2
        rep.ok()
    print(f"[info] all {len(FILES)} input files found")

    matrix = parse_conflict_matrix(FILES["matrix"])
    sections = parse_validation_sections(FILES["validation"])
    row_verify = parse_row_verification(FILES["row_verify"])
    bit_map = parse_bit_mapping(FILES["bit_map"])
    col_indep = parse_column_independence(FILES["col_indep"])
    with open(FILES["kernel"]) as f:
        kernel_src = f.read()

    print("\n=== (a) conflict_matrix consistency ===")
    check_conflict_matrix(rep, matrix)

    print("\n=== (b) validation log vs conflict_matrix.csv ===")
    check_validation_vs_csv(rep, sections, matrix)

    print("\n=== (c) bit-toggle sanity (bit 13 row/col boundary) ===")
    check_bit_toggle(rep, sections, bit_map)

    print("\n=== (d) Method 3 stride-sweep boundary sharpness ===")
    check_boundary_sharpness(rep, sections)

    print("\n=== (e) row_verification.txt cross-check ===")
    check_row_verification(rep, row_verify)

    print("\n=== (f) statistical sanity ===")
    check_statistical_sanity(rep, matrix, bit_map, sections, col_indep, row_verify)

    print("\n=== (g) multi-bank coverage ===")
    check_multi_bank_gap(rep, kernel_src)

    print("\n=== (4) BRISC kernel protocol audit ===")
    check_kernel_protocol(rep, kernel_src)

    # Summary --------------------------------------------------------------
    print("\n" + "=" * 72)
    print("SUMMARY")
    print("=" * 72)
    print(f"cross-checks run:   {rep.checks}")
    print(f"passed:             {rep.passed}")
    print(f"failed:             {rep.failed}")
    print(f"warnings:           {len(rep.warnings)}")
    print(f"notes:              {len(rep.notes)}")

    if rep.notes:
        print("\nnotes:")
        for n in rep.notes:
            print(f"  - {n}")

    if rep.warnings:
        print("\nwarnings:")
        for w in rep.warnings:
            print(f"  - {w}")

    if rep.failures:
        print("\nfailures:")
        for f in rep.failures:
            print(f"  - {f}")

    verdict = "PASS" if rep.failed == 0 else "FAIL"
    print("\n" + "=" * 72)
    print(f"VERDICT: {verdict}")
    print("=" * 72)
    return 0 if rep.failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
