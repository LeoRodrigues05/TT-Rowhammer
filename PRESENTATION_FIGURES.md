# Figures for Presentation

## Figure 1: Conflict Matrix Heatmap
**File:** `validation_8kb_matrix.png`
**Caption:** "DRAM row conflict matrix at 1KB granularity. Green cells (833 cycles) indicate same-row accesses, red cells (873 cycles) indicate cross-row accesses. The perfect 8KB block-diagonal pattern confirms an 8192-byte row size. Blue grid lines mark the 8KB row boundaries."

## Figure 2: Stride Sweep Transition
**File:** `validation_stride_sweep_8kb.png`
**Caption:** "Pipelined A-B alternating read latency vs address separation. Green points (833 cycles) are same-row; red points (873 cycles) are different-row. The transition occurs at exactly 8192 bytes, with zero ambiguity."

## Figure 3: Address Bit Classification
**File:** `validation_bit_mapping.png`
**Caption:** "Latency when toggling individual address bits. Bits 6-12 (within 8KB) produce same-row latency (833 cycles, green). Bits 13+ (crossing 8KB boundary) produce different-row latency (873-897 cycles, red). Bit 13 = 2^13 = 8192 = row size."

## Figure 4: Performance Summary
**File:** `validation_performance.png`
**Caption:** "Row buffer performance across different access patterns. Same-row accesses are consistently faster. The 40-cycle (50ns) per-access penalty for row buffer misses enables pipelined timing side-channel measurements."

## Figure 5: Validation Dashboard
**File:** `validation_dashboard.png`
**Caption:** "Summary of all 401 validation tests across 5 independent methods. 100% pass rate confirms the 8KB row size finding with high confidence."
