// SPDX-License-Identifier: Apache-2.0
// On-device DRAM row-buffer conflict timing kernel.
// Runs on BRISC (RISCV_0), measures latency of alternating DRAM reads.

#include "dataflow_api.h"
#include "debug/dprint.h"

// Read the 64-bit wall clock. Reading L latches H atomically.
inline uint64_t get_wall_clock() {
    uint32_t lo = *reinterpret_cast<volatile uint32_t*>(RISCV_DEBUG_REG_WALL_CLOCK_L);
    uint32_t hi = *reinterpret_cast<volatile uint32_t*>(RISCV_DEBUG_REG_WALL_CLOCK_H);
    return ((uint64_t)hi << 32) | lo;
}

void kernel_main() {
    // Args: addr_a, addr_b (both DRAM byte offsets into their buffer),
    //       dram_buf_base, result_buf_addr (L1), num_samples
    uint32_t dram_base    = get_arg_val<uint32_t>(0);
    uint32_t offset_a     = get_arg_val<uint32_t>(1);
    uint32_t offset_b     = get_arg_val<uint32_t>(2);
    uint32_t result_l1    = get_arg_val<uint32_t>(3);
    uint32_t num_samples  = get_arg_val<uint32_t>(4);

    // L1 scratch: two 64-byte slots for the reads
    // We'll use fixed L1 addresses just above the result buffer
    uint32_t scratch_a = result_l1 + 64 * num_samples;  // after results
    uint32_t scratch_b = scratch_a + 64;

    uint64_t noc_addr_a = get_noc_addr_from_bank_id<true>(0, dram_base + offset_a);
    uint64_t noc_addr_b = get_noc_addr_from_bank_id<true>(0, dram_base + offset_b);

    uint32_t* results = reinterpret_cast<uint32_t*>(result_l1);

    for (uint32_t i = 0; i < num_samples; i++) {
        // Step 1: open row A (warmup - not timed)
        noc_async_read(noc_addr_a, scratch_a, 64);
        noc_async_read_barrier();

        // Step 2: access B (may cause row conflict on A's bank)
        noc_async_read(noc_addr_b, scratch_b, 64);
        noc_async_read_barrier();

        // Step 3: time the re-access to A
        // If B caused a conflict, A's row was precharged; re-open is slow.
        uint64_t t0 = get_wall_clock();
        noc_async_read(noc_addr_a, scratch_a, 64);
        noc_async_read_barrier();
        uint64_t t1 = get_wall_clock();

        results[i] = (uint32_t)(t1 - t0);
    }
}
