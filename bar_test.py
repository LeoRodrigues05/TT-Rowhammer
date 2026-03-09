import fcntl, struct, os

fd = os.open('/dev/tenstorrent/0', os.O_RDWR)

IOCTL_MAGIC  = 0xFA
IOCTL_ALLOC  = (0 << 30) | (IOCTL_MAGIC << 8) | 11
IOCTL_CONFIG = (0 << 30) | (IOCTL_MAGIC << 8) | 13

TLB_4G = 0x100000000

# in(16) + out(32) = 48 bytes total
alloc_buf = bytearray(48)
struct.pack_into('QQ', alloc_buf, 0, TLB_4G, 0)
fcntl.ioctl(fd, IOCTL_ALLOC, alloc_buf)
print(f"raw: {alloc_buf.hex()}")

# out struct starts at offset 16
tlb_id      = struct.unpack_from('I', alloc_buf, 16)[0]
mmap_off_uc = struct.unpack_from('Q', alloc_buf, 24)[0]
mmap_off_wc = struct.unpack_from('Q', alloc_buf, 32)[0]
print(f"tlb_id={tlb_id}  uc=0x{mmap_off_uc:016x}  wc=0x{mmap_off_wc:016x}")

# Configure: point at DRAM channel 0, tile (x=0, y=1), addr=0, NOC0
# tenstorrent_configure_tlb_in: u32 id, u32 reserved, then tenstorrent_noc_tlb_config
# tenstorrent_noc_tlb_config: Q addr, H x_end, H y_end, H x_start, H y_start,
#                              B noc, B mcast, B ordering, B linked, B static_vc,
#                              3B reserved0, II reserved1
# Total config = 8+2+2+2+2+1+1+1+1+1+3+8 = 32 bytes
# Total cfg_in = 4+4+32 = 40 bytes, cfg_out = 8 bytes → buf = 48 bytes

cfg_buf = bytearray(48)
config = struct.pack('QHHHH BBBBB 3x II',
    0,       # addr=0 (4G-aligned)
    0, 1,    # x_end=0, y_end=1
    0, 1,    # x_start=0, y_start=1
    0,       # noc=0
    0,       # mcast=0
    1,       # ordering=1 (strict)
    0,       # linked=0
    0,       # static_vc=0
    # 3x = 3 padding bytes (reserved0)
    0, 0     # reserved1[2]
)
struct.pack_into('II', cfg_buf, 0, tlb_id, 0)
cfg_buf[8:8+len(config)] = config
print(f"config len={len(config)}, cfg_buf: {cfg_buf.hex()}")

fcntl.ioctl(fd, IOCTL_CONFIG, cfg_buf)
print("CONFIGURE_TLB OK")

import mmap
window = mmap.mmap(fd, TLB_4G, mmap.MAP_SHARED,
                   mmap.PROT_READ | mmap.PROT_WRITE,
                   offset=mmap_off_uc)
print("mmap OK")

window.seek(0)
data = window.read(64)
print(f"DRAM ch0 first 64 bytes: {data.hex()}")
window.seek(0)
window.write(b'\xAA' * 64)
window.seek(1 << 20)  # 1MB offset
window.write(b'\xBB' * 64)
window.seek(0)
assert window.read(64) == b'\xAA' * 64  # should still be AA, not BB
window.seek(0)
#window.write(bytes(64))

window.close()
os.close(fd)
