"""
Fixed DRAM row-buffer conflict timing via host-side mmap.

Uses ctypes pointer dereference instead of memoryview indexing to minimize
Python object creation overhead. Also provides a numpy-based alternative.
"""
import fcntl, struct, os, mmap, time, ctypes, sys
import numpy as np

# ── TLB setup (same as bar_test.py / time_test.py) ──────────────────────
fd = os.open('/dev/tenstorrent/0', os.O_RDWR)

IOCTL_MAGIC  = 0xFA
IOCTL_ALLOC  = (0 << 30) | (IOCTL_MAGIC << 8) | 11
IOCTL_CONFIG = (0 << 30) | (IOCTL_MAGIC << 8) | 13
TLB_4G = 0x100000000

alloc_buf = bytearray(48)
struct.pack_into('QQ', alloc_buf, 0, TLB_4G, 0)
fcntl.ioctl(fd, IOCTL_ALLOC, alloc_buf)
tlb_id      = struct.unpack_from('I', alloc_buf, 16)[0]
mmap_off_uc = struct.unpack_from('Q', alloc_buf, 24)[0]

cfg_buf = bytearray(48)
cfg_buf[8:40] = struct.pack('QHHHH BBBBB 3x II', 0, 0, 1, 0, 1, 0, 0, 1, 0, 0, 0, 0)
struct.pack_into('II', cfg_buf, 0, tlb_id, 0)
fcntl.ioctl(fd, IOCTL_CONFIG, cfg_buf)

ch0 = mmap.mmap(fd, TLB_4G, mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE, offset=mmap_off_uc)

# ── Method A: ctypes pointer dereference ────────────────────────────────
# Get the raw address of the mmap region via ctypes
_c_buf = (ctypes.c_char * TLB_4G).from_buffer(ch0)
_base_addr = ctypes.addressof(_c_buf)
ptr = ctypes.cast(_base_addr, ctypes.POINTER(ctypes.c_uint32))

def batch_latency_ctypes(addr_a, addr_b, n=50000):
    """Time n alternating reads between addr_a and addr_b using ctypes pointer.
    Returns ns per iteration (one A+B pair)."""
    ia = addr_a // 4   # uint32 indexing
    ib = addr_b // 4
    p = ptr  # local ref for speed
    t0 = time.perf_counter_ns()
    for _ in range(n):
        _ = p[ia]
        _ = p[ib]
    t1 = time.perf_counter_ns()
    return (t1 - t0) / n

# ── Method B: numpy array indexing ──────────────────────────────────────
arr = np.frombuffer(ch0, dtype=np.uint32)

def batch_latency_numpy(addr_a, addr_b, n=50000):
    """Time n alternating reads between addr_a and addr_b using numpy array.
    Returns ns per iteration (one A+B pair)."""
    ia = addr_a // 4
    ib = addr_b // 4
    a = arr  # local ref
    t0 = time.perf_counter_ns()
    for _ in range(n):
        _ = a[ia]
        _ = a[ib]
    t1 = time.perf_counter_ns()
    return (t1 - t0) / n

# ── Method C: batch via numpy fancy indexing (amortises loop overhead) ──
def batch_latency_numpy_bulk(addr_a, addr_b, n=50000):
    """Read A and B n times each using numpy vectorized access.
    Less precise per-access but eliminates Python loop overhead."""
    ia = addr_a // 4
    ib = addr_b // 4
    idx = np.array([ia, ib] * n, dtype=np.intp)
    t0 = time.perf_counter_ns()
    _ = arr[idx]           # n*2 reads in one C call
    t1 = time.perf_counter_ns()
    return (t1 - t0) / n   # ns per A+B pair

# ── Select method ───────────────────────────────────────────────────────
METHOD = os.environ.get('METHOD', 'ctypes')
if METHOD == 'numpy':
    measure = batch_latency_numpy
    print(f"Using numpy scalar indexing")
elif METHOD == 'bulk':
    measure = batch_latency_numpy_bulk
    print(f"Using numpy bulk fancy indexing")
else:
    measure = batch_latency_ctypes
    print(f"Using ctypes pointer dereference")

N = int(os.environ.get('ITERS', '50000'))

# ── Warmup ──────────────────────────────────────────────────────────────
print("Warming up...")
for _ in range(3):
    measure(0, 4096, n=1000)

# ── Quick comparison of all methods ─────────────────────────────────────
print("\n=== Method comparison (stride=4096, n=10000) ===")
print(f"  ctypes:       {batch_latency_ctypes(0, 4096, 10000):.1f} ns/iter")
print(f"  numpy scalar: {batch_latency_numpy(0, 4096, 10000):.1f} ns/iter")
print(f"  numpy bulk:   {batch_latency_numpy_bulk(0, 4096, 10000):.1f} ns/iter")

# ── Stride sweep ────────────────────────────────────────────────────────
strides = [
    0, 64, 128, 256, 512, 1024, 2048, 4096, 8192, 16384, 32768,
    65536, 128*1024, 256*1024, 512*1024,
    1024*1024, 2*1024*1024, 4*1024*1024, 8*1024*1024,
    16*1024*1024, 32*1024*1024, 64*1024*1024,
    128*1024*1024, 256*1024*1024, 512*1024*1024, 1024*1024*1024,
]

print(f"\n=== Stride sweep (method={METHOD}, n={N}) ===")
print(f"{'Stride':>14s}  {'Mean ns':>10s}  {'StdDev':>10s}")
print("-" * 40)

results = []
for stride in strides:
    if stride >= TLB_4G:
        continue
    # Take 5 independent measurements for std dev
    samples = [measure(0, stride, n=N) for _ in range(5)]
    mean_ns = np.mean(samples)
    std_ns  = np.std(samples)
    results.append((stride, mean_ns, std_ns))
    if stride < 1024:
        label = f"{stride}B"
    elif stride < 1024*1024:
        label = f"{stride//1024}KB"
    elif stride < 1024*1024*1024:
        label = f"{stride//(1024*1024)}MB"
    else:
        label = f"{stride//(1024*1024*1024)}GB"
    print(f"  {label:>12s}  {mean_ns:>10.1f}  {std_ns:>10.1f}")
    sys.stdout.flush()

# ── Plot ────────────────────────────────────────────────────────────────
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt

    strides_arr = [r[0] for r in results]
    means       = [r[1] for r in results]
    stds        = [r[2] for r in results]

    fig, ax = plt.subplots(figsize=(12, 6))
    ax.errorbar(strides_arr, means, yerr=stds, fmt='o-', capsize=3)
    ax.set_xscale('log', base=2)
    ax.set_xlabel('Stride (bytes)')
    ax.set_ylabel('Latency per A+B pair (ns)')
    ax.set_title(f'DRAM Access Latency vs Address Stride (method={METHOD}, n={N})')
    ax.grid(True, which='both', alpha=0.3)
    fig.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/row_conflict_sweep_python.png', dpi=150)
    print(f"\nPlot saved to rowhammer/row_conflict_sweep_python.png")
except ImportError:
    print("\nmatplotlib not available, skipping plot")

# ── Cleanup ─────────────────────────────────────────────────────────────
ch0.close()
os.close(fd)
