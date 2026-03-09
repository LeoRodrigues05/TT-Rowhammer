import fcntl, struct, os, mmap, time, ctypes

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
cfg_buf[8:40] = struct.pack('QHHHH BBBBB 3x II', 0, 0,1, 0,1, 0, 0, 1, 0, 0, 0, 0)
struct.pack_into('II', cfg_buf, 0, tlb_id, 0)
fcntl.ioctl(fd, IOCTL_CONFIG, cfg_buf)
ch0 = mmap.mmap(fd, TLB_4G, mmap.MAP_SHARED,
                mmap.PROT_READ | mmap.PROT_WRITE, offset=mmap_off_uc)

mv = memoryview(ch0)
N = 50000

def batch_latency_ns(addr_a, addr_b, n=N):
    """Time n alternating accesses between addr_a and addr_b. Returns ns per iteration."""
    t0 = time.perf_counter_ns()
    for _ in range(n):
        _ = mv[addr_a]
        _ = mv[addr_b]
    t1 = time.perf_counter_ns()
    return (t1 - t0) / n

print("Batch timing (ns per A+B iteration pair):")
print(f"  (0, 0)        same addr:    {batch_latency_ns(0, 0):.1f} ns")
print(f"  (0, 256)      +256B:        {batch_latency_ns(0, 256):.1f} ns")
print(f"  (0, 2048)     +2KB:         {batch_latency_ns(0, 2048):.1f} ns")
print(f"  (0, 65536)    +64KB:        {batch_latency_ns(0, 65536):.1f} ns")
print(f"  (0, 1<<20)    +1MB:         {batch_latency_ns(0, 1<<20):.1f} ns")
print(f"  (0, 1<<26)    +64MB:        {batch_latency_ns(0, 1<<26):.1f} ns")
print(f"  (0, 1<<29)    +512MB:       {batch_latency_ns(0, 1<<29):.1f} ns")
print(f"  (0, 1<<30)    +1GB:         {batch_latency_ns(0, 1<<30):.1f} ns")

# Also try: does Python loop overhead dominate? Measure with C extension timing.
# Quick check: same addr repeated should be near 2x single latency (~1860ns/iter)
# If we see much higher on some strides, that's the conflict signal.

ch0.close()
os.close(fd)
