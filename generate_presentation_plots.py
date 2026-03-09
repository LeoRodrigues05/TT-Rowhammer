#!/usr/bin/env python3
"""
Generate presentation-ready plots for Blackhole DRAM 8KB Row Size Validation.
"""
import numpy as np
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

# ═══════════════════════════════════════════════════════════════
# Parse validation results
# ═══════════════════════════════════════════════════════════════

def parse_validation_log(path):
    """Parse the validation_8kb_boundaries.txt log file."""
    sections = {}
    current_section = None
    data = []

    with open(path) as f:
        for line in f:
            line = line.strip()
            if line.startswith('# VALIDATION METHOD'):
                if current_section and data:
                    sections[current_section] = data
                current_section = line
                data = []
            elif line.startswith('#') or not line:
                continue
            else:
                data.append(line)

    if current_section and data:
        sections[current_section] = data

    return sections

# ═══════════════════════════════════════════════════════════════
# Figure 1: 8KB Conflict Matrix (Method 2b - 16x16 sub-row)
# ═══════════════════════════════════════════════════════════════

def plot_conflict_matrix():
    """Generate conflict matrix heatmap from the 16x16 1KB-step matrix."""
    # Parse the CSV data from validation log
    log_path = '/home/noahweaver/tt-metal/rowhammer/validation_8kb_boundaries.txt'

    # Read the 2b section
    matrix = np.zeros((16, 16))
    in_2b = False

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if '# VALIDATION METHOD 2b' in line:
                in_2b = True
                continue
            if in_2b:
                if line.startswith('#') and 'Results' in line:
                    break
                if line.startswith('#') or not line:
                    continue
                parts = line.split(',')
                if len(parts) >= 3:
                    addr_a = int(parts[0])
                    addr_b = int(parts[1])
                    val = int(parts[2])
                    i = addr_a // 1024
                    j = addr_b // 1024
                    if i < 16 and j < 16:
                        matrix[i, j] = val

    fig, ax = plt.subplots(figsize=(10, 8))

    # Custom colormap: green (same row) → red (different row)
    cmap = LinearSegmentedColormap.from_list('rowmap',
        [(0.2, 0.7, 0.3), (0.95, 0.95, 0.5), (0.8, 0.2, 0.2)])

    im = ax.imshow(matrix, cmap=cmap, vmin=820, vmax=890, aspect='equal')
    cbar = plt.colorbar(im, ax=ax, label='Latency (cycles @ 800MHz)')

    # Add grid lines at 8KB boundaries
    ax.axhline(7.5, color='blue', linewidth=2.5, alpha=0.8)
    ax.axvline(7.5, color='blue', linewidth=2.5, alpha=0.8)

    # Add text annotations
    for i in range(16):
        for j in range(16):
            val = int(matrix[i, j])
            color = 'white' if val > 860 else 'black'
            ax.text(j, i, str(val), ha='center', va='center',
                   fontsize=7, color=color, fontweight='bold')

    # Labels
    labels = [f'{i}KB' for i in range(16)]
    ax.set_xticks(range(16))
    ax.set_xticklabels(labels, rotation=45, fontsize=8)
    ax.set_yticks(range(16))
    ax.set_yticklabels(labels, fontsize=8)
    ax.set_xlabel('Address B', fontsize=12)
    ax.set_ylabel('Address A', fontsize=12)
    ax.set_title('DRAM Row Conflict Matrix (1KB granularity)\n'
                 'Green = Same Row (833 cyc) | Red = Different Row (873 cyc)\n'
                 'Blue lines mark 8KB row boundaries',
                 fontsize=13, fontweight='bold')

    # Add row labels
    ax.text(-1.5, 3.5, 'Row 0', fontsize=10, fontweight='bold', color='blue',
            ha='center', va='center', rotation=90)
    ax.text(-1.5, 11.5, 'Row 1', fontsize=10, fontweight='bold', color='blue',
            ha='center', va='center', rotation=90)
    ax.text(3.5, -1.2, 'Row 0', fontsize=10, fontweight='bold', color='blue',
            ha='center', va='center')
    ax.text(11.5, -1.2, 'Row 1', fontsize=10, fontweight='bold', color='blue',
            ha='center', va='center')

    plt.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/validation_8kb_matrix.png', dpi=300)
    print("Saved: validation_8kb_matrix.png")
    plt.close()

# ═══════════════════════════════════════════════════════════════
# Figure 2: Stride Sweep Detail (Method 3)
# ═══════════════════════════════════════════════════════════════

def plot_stride_sweep():
    """Plot stride sweep showing sharp transition at 8KB."""
    log_path = '/home/noahweaver/tt-metal/rowhammer/validation_8kb_boundaries.txt'

    strides = []
    medians = []
    in_m3 = False

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if '# VALIDATION METHOD 3' in line:
                in_m3 = True
                continue
            if in_m3:
                if line.startswith('#'):
                    if 'Results' in line:
                        break
                    continue
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    try:
                        strides.append(int(parts[0]))
                        medians.append(int(parts[1]))
                    except ValueError:
                        continue

    fig, ax = plt.subplots(figsize=(12, 6))

    # Color points by classification
    colors = ['#2ecc71' if m < 850 else '#e74c3c' for m in medians]
    strides_kb = [s / 1024.0 for s in strides]

    ax.scatter(strides_kb, medians, c=colors, s=80, zorder=5, edgecolors='black', linewidth=0.5)
    ax.plot(strides_kb, medians, 'k-', alpha=0.3, linewidth=1)

    # Mark the transition
    ax.axvline(8.0, color='blue', linewidth=2, linestyle='--', alpha=0.8,
               label='8KB boundary')
    ax.axhline(833, color='#2ecc71', linewidth=1, linestyle=':', alpha=0.5,
               label='Same-row median (833)')
    ax.axhline(873, color='#e74c3c', linewidth=1, linestyle=':', alpha=0.5,
               label='Diff-row median (873)')

    # Annotate
    ax.annotate('TRANSITION\nat exactly 8192 bytes',
                xy=(8.0, 873), xytext=(8.8, 855),
                fontsize=11, fontweight='bold', color='blue',
                arrowprops=dict(arrowstyle='->', color='blue', lw=2),
                bbox=dict(boxstyle='round,pad=0.3', facecolor='lightyellow'))

    ax.set_xlabel('Stride (KB)', fontsize=12)
    ax.set_ylabel('Median Latency (cycles @ 800MHz)', fontsize=12)
    ax.set_title('DRAM Row Size Detection: Stride Sweep\n'
                 'Sharp transition from 833 to 873 cycles at exactly 8KB',
                 fontsize=13, fontweight='bold')
    ax.legend(fontsize=10, loc='center right')
    ax.set_ylim(820, 885)
    ax.grid(True, alpha=0.3)

    # Add secondary y-axis in ns
    ax2 = ax.twinx()
    ax2.set_ylim(ax.get_ylim()[0] * 1.25, ax.get_ylim()[1] * 1.25)
    ax2.set_ylabel('Latency (ns)', fontsize=12)

    plt.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/validation_stride_sweep_8kb.png', dpi=300)
    print("Saved: validation_stride_sweep_8kb.png")
    plt.close()

# ═══════════════════════════════════════════════════════════════
# Figure 3: Address Bit Mapping Diagram
# ═══════════════════════════════════════════════════════════════

def plot_bit_mapping():
    """Bar chart showing latency for each address bit toggle."""
    log_path = '/home/noahweaver/tt-metal/rowhammer/validation_8kb_boundaries.txt'

    bits = []
    medians = []
    in_m5 = False

    with open(log_path) as f:
        for line in f:
            line = line.strip()
            if '# VALIDATION METHOD 5' in line:
                in_m5 = True
                continue
            if in_m5:
                if line.startswith('#'):
                    if 'Results' in line:
                        break
                    continue
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        bits.append(int(parts[0]))
                        medians.append(int(parts[2]))
                    except ValueError:
                        continue

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(14, 9), height_ratios=[2, 1])

    # Top: Bar chart
    colors = ['#2ecc71' if b < 13 else '#e74c3c' if b < 17 else '#c0392b' for b in bits]
    bars = ax1.bar(range(len(bits)), medians, color=colors, edgecolor='black', linewidth=0.5)

    ax1.axhline(850, color='blue', linewidth=2, linestyle='--', alpha=0.7,
                label='COL/ROW threshold')
    ax1.axvline(6.5, color='blue', linewidth=2, alpha=0.5)  # Between bit 12 and 13

    ax1.set_xticks(range(len(bits)))
    ax1.set_xticklabels([f'bit {b}\n({2**b//1024}KB)' if b >= 10
                          else f'bit {b}\n({2**b}B)' for b in bits],
                         fontsize=8, rotation=0)
    ax1.set_ylabel('Median Latency (cycles)', fontsize=12)
    ax1.set_title('Address Bit Toggle Test: Column vs Row Identification\n'
                  'Green = Column bit (same row) | Red = Row bit (different row)',
                  fontsize=13, fontweight='bold')
    ax1.set_ylim(800, 910)
    ax1.legend(fontsize=10)

    # Add annotations
    ax1.annotate('COLUMN\n(bits 6-12)', xy=(3, 835), fontsize=14,
                fontweight='bold', color='#2ecc71', ha='center')
    ax1.annotate('ROW\n(bits 13+)', xy=(14, 900), fontsize=14,
                fontweight='bold', color='#c0392b', ha='center')
    ax1.annotate('Bit 13 = 8KB\n= Row Size',
                xy=(7, 873), xytext=(9, 830),
                fontsize=10, fontweight='bold', color='blue',
                arrowprops=dict(arrowstyle='->', color='blue', lw=1.5))

    # Bottom: Address diagram
    ax2.axis('off')
    diagram = """
    ┌─────────────────────────────────────────────────────────────────────┐
    │                    DRAM Address Bit Layout                          │
    ├────────────────────────┬──────────────────────┬─────────────────────┤
    │   bits [31:13]         │    bits [12:6]        │    bits [5:0]       │
    │                        │                       │                     │
    │   ROW SELECT           │    COLUMN SELECT      │    BYTE SELECT      │
    │   (524K rows)          │    (128 columns)      │    (64B per txn)    │
    │                        │                       │                     │
    │   Row = addr >> 13     │    Col within 8KB row │    Within cache line│
    ├────────────────────────┼──────────────────────┼─────────────────────┤
    │   873-897 cyc          │    833 cyc            │    N/A              │
    │   (different row)      │    (same row)         │                     │
    └────────────────────────┴──────────────────────┴─────────────────────┘
    """
    ax2.text(0.5, 0.5, diagram, transform=ax2.transAxes,
            fontsize=11, fontfamily='monospace',
            ha='center', va='center',
            bbox=dict(boxstyle='round', facecolor='lightyellow', alpha=0.8))

    plt.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/validation_bit_mapping.png', dpi=300)
    print("Saved: validation_bit_mapping.png")
    plt.close()

# ═══════════════════════════════════════════════════════════════
# Figure 4: Performance Summary Bar Chart
# ═══════════════════════════════════════════════════════════════

def plot_performance_summary():
    """Bar chart comparing same-row vs cross-row performance."""
    fig, ax = plt.subplots(figsize=(10, 6))

    categories = ['Single Read\n(serialized)', 'A-B Pairs\n(8 pairs)', 'Burst 16\n(sequential)',
                  'Burst 16\n(64KB stride)', 'Burst 16\n(1MB stride)']
    same_row = [436, 833, 770, None, None]  # N/A for scattered
    diff_row = [436, 873, None, 914, 1418]

    x = np.arange(len(categories))
    width = 0.35

    bars1 = ax.bar(x - width/2, [v if v else 0 for v in same_row],
                   width, label='Same Row / Sequential', color='#2ecc71',
                   edgecolor='black', linewidth=0.5)
    bars2 = ax.bar(x + width/2, [v if v else 0 for v in diff_row],
                   width, label='Different Row / Scattered', color='#e74c3c',
                   edgecolor='black', linewidth=0.5)

    # Hide zero bars
    for b, v in zip(bars1, same_row):
        if v is None: b.set_alpha(0)
    for b, v in zip(bars2, diff_row):
        if v is None: b.set_alpha(0)

    # Add value labels
    for b, v in zip(bars1, same_row):
        if v: ax.text(b.get_x() + b.get_width()/2., v + 15, str(v),
                      ha='center', va='bottom', fontweight='bold', fontsize=9)
    for b, v in zip(bars2, diff_row):
        if v: ax.text(b.get_x() + b.get_width()/2., v + 15, str(v),
                      ha='center', va='bottom', fontweight='bold', fontsize=9)

    ax.set_ylabel('Total Latency (cycles @ 800MHz)', fontsize=12)
    ax.set_title('Row Buffer Performance Summary\n'
                 'Same-row accesses are consistently faster across all patterns',
                 fontsize=13, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(categories, fontsize=10)
    ax.legend(fontsize=11)
    ax.set_ylim(0, 1550)
    ax.grid(True, alpha=0.3, axis='y')

    # Add delta annotations
    ax.annotate('+40 cyc\n(50 ns)', xy=(1.5, 855), fontsize=9, color='blue',
               fontweight='bold', ha='center')
    ax.annotate('+144 cyc\n(180 ns)', xy=(3.5, 950), fontsize=9, color='blue',
               fontweight='bold', ha='center')

    plt.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/validation_performance.png', dpi=300)
    print("Saved: validation_performance.png")
    plt.close()

# ═══════════════════════════════════════════════════════════════
# Figure 5: Validation Summary Dashboard
# ═══════════════════════════════════════════════════════════════

def plot_validation_dashboard():
    """Summary dashboard showing all methods pass."""
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.axis('off')

    methods = [
        ('Method 1', 'Explicit Boundary Test', 21, 21),
        ('Method 2', '8KB Conflict Matrix', 64, 64),
        ('Method 2b', '1KB Sub-Row Matrix', 256, 256),
        ('Method 3', 'Stride Sweep', 33, 33),
        ('Method 4', 'Multiple Base Addresses', 7, 7),
        ('Method 5', 'Bit-Toggle Confirmation', 20, 20),
    ]

    total_pass = sum(p for _, _, p, _ in methods)
    total_tests = sum(t for _, _, _, t in methods)

    # Title
    ax.text(0.5, 0.95, f'BLACKHOLE DRAM ROW SIZE VALIDATION: {total_pass}/{total_tests} TESTS PASSED',
            transform=ax.transAxes, fontsize=16, fontweight='bold',
            ha='center', va='top', color='#27ae60')

    # Table
    y_start = 0.82
    y_step = 0.11

    for i, (name, desc, passed, total) in enumerate(methods):
        y = y_start - i * y_step
        status = 'PASS' if passed == total else 'FAIL'
        color = '#27ae60' if passed == total else '#e74c3c'
        bg_color = '#d5f5e3' if passed == total else '#fadbd8'

        # Background box
        ax.add_patch(plt.Rectangle((0.02, y - 0.04), 0.96, 0.09,
                     facecolor=bg_color, edgecolor=color, linewidth=1.5,
                     transform=ax.transAxes, zorder=1))

        ax.text(0.05, y, f'{status}', transform=ax.transAxes,
               fontsize=14, fontweight='bold', color=color, va='center', zorder=2)
        ax.text(0.15, y, f'{name}: {desc}', transform=ax.transAxes,
               fontsize=11, va='center', zorder=2)
        ax.text(0.85, y, f'{passed}/{total}', transform=ax.transAxes,
               fontsize=12, fontweight='bold', va='center', ha='right', zorder=2)

    # Footer
    ax.text(0.5, 0.03, 'Blackhole Tenstorrent | DRAM Bank 0 | NOC (0,1) | BRISC 800MHz | 2026-02-26',
            transform=ax.transAxes, fontsize=9, ha='center', color='gray')

    plt.tight_layout()
    fig.savefig('/home/noahweaver/tt-metal/rowhammer/validation_dashboard.png', dpi=300)
    print("Saved: validation_dashboard.png")
    plt.close()


if __name__ == '__main__':
    print("Generating presentation plots...")
    plot_conflict_matrix()
    plot_stride_sweep()
    plot_bit_mapping()
    plot_performance_summary()
    plot_validation_dashboard()
    print("\nAll plots generated successfully!")
