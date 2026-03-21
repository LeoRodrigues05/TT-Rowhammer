# Blackhole DRAM Geometry & Rowhammer Research

> Characterizing LPDDR5 row structure on the Tenstorrent Blackhole chip as a prerequisite for rowhammer experimentation.

---

## TL;DR

We empirically determined that the Tenstorrent Blackhole's LPDDR5 DRAM has an **8KB row size**, confirmed across 401 independent tests with a 100% pass rate. The address mapping is simple (no XOR interleaving), the chip operates in **open-page mode**, and the hammer rate via pipelined NOC reads is ~**14–17M activations/second** — enough to exceed typical rowhammer thresholds by 25–100×.

---

## Background

Rowhammer attacks work by repeatedly activating (hammering) DRAM rows adjacent to a victim row, inducing bit flips through electrical interference. To do this effectively on new hardware, you first need to understand:

1. **Row size** — how many bytes map to a single DRAM row
2. **Address mapping** — how byte addresses translate to (row, column) coordinates
3. **Page mode** — whether the row buffer stays open between accesses (open-page) or is closed after each access (closed-page)

This repo answers all three questions for the Blackhole chip.

---

## Key Findings

| Parameter | Value |
|-----------|-------|
| Row size | **8192 bytes (8KB)** |
| Address mapping | **Sequential, no XOR hashing** |
| Page mode | **Open-page** |
| Same-row latency | 833 cycles (1041 ns @ 800MHz BRISC) |
| Cross-row latency | 873 cycles (1091 ns) |
| Row buffer penalty | **40 cycles (50 ns) per access** |
| Pipelined hammer rate | **~14–17M activations/sec** |
| Acts per 32ms refresh window | ~450K–550K |
| Typical TRH threshold | 5K–20K (exceeded by 25–100×) |

### Address Bit Layout

```
Byte address within DRAM bank:

 Bits [31:13]       Bits [12:6]       Bits [5:0]
 ┌──────────────┐  ┌──────────────┐  ┌──────────┐
 │  ROW SELECT  │  │   COLUMN     │  │  BYTE    │
 │  (524K rows) │  │  (128 cols)  │  │  OFFSET  │
 └──────────────┘  └──────────────┘  └──────────┘
  Row = addr >> 13   64B per column    Within cache line
```

- **Row N** spans addresses `[N × 8192, (N+1) × 8192 − 1]`
- **Adjacent rows** are at ±`0x2000` offsets
- **Double-sided hammer pattern**: victim at `V`, aggressors at `V − 0x2000` and `V + 0x2000`

---

## Validation Summary

Five independent methods, all agreeing:

| Method | Description | Tests | Result |
|--------|-------------|-------|--------|
| 1 | Explicit boundary test (21 address pairs) | 21/21 | ✅ PASS |
| 2 | 8KB conflict matrix (8×8 at 8KB step) | 64/64 | ✅ PASS |
| 2b | Sub-row matrix (16×16 at 1KB step) | 256/256 | ✅ PASS |
| 3 | Stride sweep (6KB–10KB, 128B steps) | 33/33 | ✅ PASS |
| 4 | Multiple base addresses (0 to 512MB) | 7/7 | ✅ PASS |
| 5 | Bit-toggle classification (bits 6–25) | 20/20 | ✅ PASS |
| | **TOTAL** | **401/401** | **✅ 100%** |

---

## Methodology

All timing was done **on-device** using a BRISC kernel (not from the host CPU), which is critical — host-side Python mmap reads mask row buffer effects due to CPU caching and PCIe latency overhead.

**Measurement protocol (per test):**
1. Read address A → opens A's row in the row buffer (warmup, not timed)
2. Read address B → may cause A's row to be precharged (eviction)
3. Time re-read of A → **if B evicted A, this is slow (873 cyc); if same row, fast (833 cyc)**

Each measurement: 8 alternating A–B pairs, median of 32–64 samples. Row buffer flushed between measurements by reading from `addr + 128MB`.

---

## Repo Structure

```
.
├── README.md                          ← You are here
│
├── kernels/
│   └── dram_latency_timer.cpp         ← On-device BRISC kernel (core timing logic)
├── dram_latency.cpp                   ← Alternate/earlier version of BRISC kernel
│
├── bar_test.py                        ← Low-level TLB + mmap setup for /dev/tenstorrent/0
├── time_test.py                       ← Host-side timing attempt (superseded)
├── time_test_fixed.py                 ← Improved host-side timing (ctypes/numpy)
├── row_conflict_analysis.py           ← Initial closed-page mode analysis
├── generate_presentation_plots.py     ← Generates all 5 figures from validation data
│
├── validation_8kb_boundaries.txt      ← Raw output of all 401 validation tests
├── conflict_matrix.csv                ← 16×16 matrix at 1KB granularity
├── address_bit_mapping.txt            ← Per-bit toggle latency (bits 6–25)
├── row_size_determination.txt         ← Burst read stride sweep data
├── row_verification.txt               ← Explicit boundary pair measurements
├── validation_column_independence.txt ← Confirms all columns within a row behave identically
│
├── PRESENTATION_SUMMARY.md            ← Full validated findings (start here for deep dive)
├── PRESENTATION_FIGURES.md            ← Figure captions for the 5 generated plots
├── REPRODUCTION_GUIDE.md              ← Step-by-step build & run instructions
├── summary_dram_geometry.md           ← Technical reference: address layout, row buffer behavior
│
└── generated/
    ├── inspector/                     ← tt-metal inspector logs (kernel compile, device lifecycle)
    └── watcher/                       ← Kernel ELF paths and names
```

**Start reading:** `PRESENTATION_SUMMARY.md` for a complete technical writeup, or `summary_dram_geometry.md` for a concise reference card.

---

## How the BRISC Kernel Works

The core timing kernel (`kernels/dram_latency_timer.cpp`) runs on the **BRISC** (RISC-V core) at 800MHz directly on the Blackhole chip. It reads the chip's hardware wall clock to get cycle-accurate timing unaffected by host CPU or PCIe jitter.

```cpp
// Simplified logic
noc_async_read(addr_a, scratch_a, 64);   // Open row A (warmup)
noc_async_read_barrier();
noc_async_read(addr_b, scratch_b, 64);   // Possibly evict A
noc_async_read_barrier();

uint64_t t0 = get_wall_clock();
noc_async_read(addr_a, scratch_a, 64);   // Time re-access to A
noc_async_read_barrier();
uint64_t t1 = get_wall_clock();

results[i] = (uint32_t)(t1 - t0);       // 833 = same row, 873 = different row
```

---

## Reproducing the Results

**Prerequisites:** Tenstorrent Blackhole hardware, tt-metal SDK, cmake + clang, Python 3.8+

```bash
# Build
cd ~/tt-metal
cmake --build build --target metal_example_validation_test -j$(nproc)

# Run all 401 validation tests (~1-2 seconds)
./build/programming_examples/metal_example_validation_test
# Expected: ALL TESTS PASSED (401/401)

# Generate plots
cd rowhammer
python3 generate_presentation_plots.py
```

See `REPRODUCTION_GUIDE.md` for full instructions including the row mapping discovery and page mode tests.

---

## Rowhammer Next Steps

The characterization phase is complete. What's needed next:

1. **Write a hammer kernel** — rapidly alternate between two aggressor rows at `V − 0x2000` and `V + 0x2000`
2. **Write a victim check kernel** — read the victim row and check for bit flips
3. **Identify vulnerable bit positions** — run across many victim row addresses
4. **Build an exploit** — map target data (e.g. model weights, page tables) to vulnerable rows

The hammer rate of ~14–17M activations/sec with pipelined NOC reads should comfortably exceed the threshold needed to induce bit flips.

---

## Hardware & Software

| Component | Details |
|-----------|---------|
| Hardware | Tenstorrent Blackhole (4GB LPDDR5 per bank, 8 banks) |
| BRISC clock | 800MHz (1 cycle = 1.25ns) |
| Target | DRAM Bank 0, NOC endpoint (0,1) |
| SDK | tt-metal |
| OS | Ubuntu 24.04 |
| Date | 2026-02-26 |

---

## Related Work

This project is directly motivated by [GPUHammer](https://github.com/sith-lab/gpuhammer) (SEC '25), which demonstrated Rowhammer bit flips on NVIDIA RTX A6000 GDDR6 memory and used them to corrupt ML model weights. See `SEC25_GPUHammer2.pdf` and `sandpsubmissiongpuhammer.pdf` in this repo for the full paper.
