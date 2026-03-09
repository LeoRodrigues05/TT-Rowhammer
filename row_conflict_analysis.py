"""
Analysis of DRAM row-buffer conflict timing results from Blackhole Tenstorrent chip.

Results from on-device BRISC kernel with cycle-accurate wall clock timing.
BRISC clock: 800MHz (1 cycle = 1.25ns)
"""
import numpy as np

# ═══════════════════════════════════════════════════════════════════════
# RAW RESULTS from metal_example_dram_latency run
# ═══════════════════════════════════════════════════════════════════════

# TEST 1: Same-port stride sweep on DRAM (0,1), raw addresses
# A = addr 0, B = addr 0+stride, same NOC port
test1_strides = [
    0, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768,
    65536, 131072, 262144, 524288, 1048576, 2*1024*1024,
    4*1024*1024, 8*1024*1024, 16*1024*1024, 32*1024*1024,
    64*1024*1024, 128*1024*1024, 256*1024*1024, 512*1024*1024,
    1024*1024*1024, 2048*1024*1024]

# All medians = 436, all p25 = 428, all p75 = 436
test1_medians = [436] * len(test1_strides)

# TEST 2: Cross-port comparison
# Same bank (0), different NOC ports: NO difference
# Different banks: NO difference
# All medians = 436

# TEST 3: Fine-grained sweep (0-16KB in 64B steps)
# ALL medians = 436

# TEST 4: Self-read baseline (A=B)
# All medians = 436

print("═" * 70)
print("DRAM ROW-BUFFER CONFLICT ANALYSIS - BLACKHOLE TENSTORRENT")
print("═" * 70)
print()
print("MEASUREMENT METHODOLOGY:")
print("  - On-device BRISC kernel reads DRAM via NOC")
print("  - Timing: BRISC wall clock (800MHz, cycle-accurate)")
print("  - Protocol: read A, read B (eviction), time read A again")
print("  - 256 samples per measurement, reporting median")
print()
print("KEY FINDINGS:")
print()
print("1. ALL latencies are identical regardless of stride:")
print("   - Median: 436 cycles (545.0 ns)")
print("   - P25:    428 cycles (535.0 ns)")
print("   - Max:    ~564 cycles (705 ns) with occasional spikes to 996+")
print()
print("2. Cross-bank access shows NO latency difference:")
print("   - Same port, same addr:        436 cycles")
print("   - Same bank, diff NOC port:    436 cycles")
print("   - Different bank (0 vs 1):     436 cycles")
print("   - Different bank (0 vs 4):     436 cycles")
print()
print("3. Self-read baseline (A=B, no eviction):")
print("   - Same 436 cycles across all addresses (0 to 2GB)")
print()

print("INTERPRETATION:")
print()
print("The DRAM controller appears to be operating in CLOSED-PAGE mode.")
print("In closed-page mode, the row buffer is precharged after every")
print("access. This means:")
print("  a) There are no row buffer HITS (every access is a miss)")
print("  b) There are no row buffer CONFLICTS (row is already closed)")
print("  c) Every access pays the full ACTIVATE + READ + PRECHARGE cost")
print()
print("This explains why:")
print("  - Stride doesn't matter (no row locality to exploit)")
print("  - Cross-bank doesn't matter (independent controllers)")
print("  - Self-reads don't benefit (row is closed between accesses)")
print()
print("The consistent 436 cycles = 545 ns represents:")
print("  tRC (row cycle) ≈ 45-60ns (LPDDR5 spec)")
print("  + NOC round-trip latency (~200-300ns)")
print("  + Read pipeline latency (~100-200ns)")
print("  = ~400-600ns total (our 545ns is within range)")
print()

print("IMPLICATIONS FOR ROWHAMMER:")
print()
print("  - Classic rowhammer (TRRespass-style) requires OPEN-PAGE mode")
print("    to create row conflicts. In closed-page mode, every access")
print("    already activates the row, so there's no way to preferentially")
print("    hammer one row more than its neighbors.")
print()
print("  - However, the DRAM cells are still activated on every access.")
print("    Closed-page mode means we're actually hammering rows on EVERY")
print("    access (since every access = ACTIVATE + READ + PRECHARGE).")
print()
print("  - To perform rowhammer in closed-page mode:")
print("    1. We need to know the address-to-row mapping within the bank")
print("    2. We need to rapidly alternate accesses to two rows adjacent")
print("       to the victim row (double-sided rowhammer)")
print("    3. The limiting factor is tRC (~45-60ns per activation)")
print("    4. At 545ns per access, we get ~1.8M activations/second")
print("       per bank (through the NOC)")
print()
print("NEXT STEPS:")
print()
print("  1. Determine the row size by examining LPDDR5 specs or")
print("     the DRAM controller configuration registers")
print("  2. Write a direct hammering kernel that rapidly alternates")
print("     between two addresses in the same bank")
print("  3. Use a separate read pass to check for bit flips in")
print("     the victim row between the two aggressor rows")
print("  4. The 545ns/access gives an upper bound on hammer rate;")
print("     we may be able to pipeline multiple outstanding reads")
print("     to increase throughput")
print()

# Generate plot
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(12, 6))

    # Plot all strides
    ax.semilogx(test1_strides, test1_medians, 'o-', markersize=4, label='Median latency')
    ax.axhline(y=436, color='r', linestyle='--', alpha=0.5, label='Constant 436 cycles')
    ax.axhline(y=428, color='b', linestyle=':', alpha=0.3, label='Min (428 cycles)')

    ax.set_xlabel('Stride (bytes)')
    ax.set_ylabel('Latency (cycles @ 800MHz)')
    ax.set_title('DRAM Access Latency vs Address Stride\n'
                 'Blackhole Tenstorrent - CLOSED-PAGE mode (no row buffer effect)')

    # Add ns scale on right
    ax2 = ax.twinx()
    ax2.set_ylim(ax.get_ylim()[0] * 1.25, ax.get_ylim()[1] * 1.25)
    ax2.set_ylabel('Latency (ns)')

    ax.legend()
    ax.grid(True, which='both', alpha=0.3)
    fig.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/row_conflict_sweep.png', dpi=150)
    print("Plot saved to rowhammer/row_conflict_sweep.png")
except Exception as e:
    print(f"Could not generate plot: {e}")
