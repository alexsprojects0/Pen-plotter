#!/usr/bin/env python3
"""
Previews what the pen plotter would draw.
Uses the same SVG parsing as svg_to_plotter.py.

Usage:
    python simulate_plotter.py (filename).svg
    python simulate_plotter.py (filename).svg --animate
"""

import argparse
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.animation import FuncAnimation, PillowWriter

from svg_to_plotter import (
    load_svg_as_polylines,
    convert_units_to_mm,
    fit_to_bed,
    BED_X_MM,
    BED_Y_MM,
    MARGIN_MM,
)


def build_segments(fitted_polylines):
    segments = []
    cur = (0.0, 0.0)
    for pl in fitted_polylines:
        if not pl:
            continue
        segments.append((cur[0], cur[1], pl[0][0], pl[0][1], False))
        for i in range(len(pl) - 1):
            x0, y0 = pl[i]
            x1, y1 = pl[i + 1]
            segments.append((x0, y0, x1, y1, True))
        cur = pl[-1]
    segments.append((cur[0], cur[1], 0.0, 0.0, False))
    return segments


def draw_bed_outline(ax):
    ax.add_patch(plt.Rectangle((0, 0), BED_X_MM, BED_Y_MM,
                                fill=False, edgecolor="#888", linewidth=1.5))
    ax.add_patch(plt.Rectangle((MARGIN_MM, MARGIN_MM),
                                BED_X_MM - 2 * MARGIN_MM,
                                BED_Y_MM - 2 * MARGIN_MM,
                                fill=False, edgecolor="#ccc", linewidth=1,
                                linestyle=":"))


def make_static_preview(segments, out_path):
    fig, ax = plt.subplots(figsize=(6, 9))
    draw_bed_outline(ax)

    for x0, y0, x1, y1, drawing in segments:
        if drawing:
            ax.plot([x0, x1], [y0, y1], color="black", linewidth=1.2, zorder=2)
        else:
            ax.plot([x0, x1], [y0, y1], color="#e07070", linewidth=0.6,
                    linestyle="--", zorder=1)

    ax.set_xlim(-5, BED_X_MM + 5)
    ax.set_ylim(-5, BED_Y_MM + 5)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_title("Plotter preview (solid = drawing, dashed red = pen-up travel)")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")
    fig.tight_layout()
    fig.savefig(out_path, dpi=150)
    plt.close(fig)


def make_animated_preview(segments, out_path, fps=30, seconds=12):
    fig, ax = plt.subplots(figsize=(6, 9))
    draw_bed_outline(ax)
    ax.set_xlim(-5, BED_X_MM + 5)
    ax.set_ylim(-5, BED_Y_MM + 5)
    ax.invert_yaxis()
    ax.set_aspect("equal")
    ax.set_title("Plotter simulation")
    ax.set_xlabel("X (mm)")
    ax.set_ylabel("Y (mm)")

    pen_dot, = ax.plot([], [], "o", color="crimson", markersize=6, zorder=3)
    drawn_lines = []

    total_frames = fps * seconds
    n_segments = len(segments)

    def frame_to_progress(frame):
        return (frame / max(1, total_frames - 1)) * n_segments

    def init():
        pen_dot.set_data([], [])
        return [pen_dot]

    def update(frame):
        progress = frame_to_progress(frame)
        full_segments = int(progress)
        partial = progress - full_segments

        while len(drawn_lines) < full_segments and len(drawn_lines) < n_segments:
            x0, y0, x1, y1, drawing = segments[len(drawn_lines)]
            if drawing:
                ax.plot([x0, x1], [y0, y1], color="black", linewidth=1.2, zorder=2)
            else:
                ax.plot([x0, x1], [y0, y1], color="#e07070", linewidth=0.6,
                        linestyle="--", zorder=1)
            drawn_lines.append(True)

        if full_segments < n_segments:
            x0, y0, x1, y1, drawing = segments[full_segments]
            px = x0 + (x1 - x0) * partial
            py = y0 + (y1 - y0) * partial
        elif segments:
            px, py = segments[-1][2], segments[-1][3]
        else:
            px, py = 0.0, 0.0

        pen_dot.set_data([px], [py])
        return [pen_dot]

    anim = FuncAnimation(fig, update, frames=total_frames, init_func=init,
                          blit=False, interval=1000 / fps)
    anim.save(out_path, writer=PillowWriter(fps=fps))
    plt.close(fig)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("svg_file", help="Path to SVG line art")
    ap.add_argument("--out", default="preview.png")
    ap.add_argument("--animate", action="store_true")
    ap.add_argument("--animate-out", default="preview.gif")
    ap.add_argument("--no-fit", action="store_true")
    ap.add_argument("--scale", type=float, default=None)
    args = ap.parse_args()

    raw_polylines = load_svg_as_polylines(args.svg_file)
    if not raw_polylines:
        print("No drawable lines/paths found in that SVG.", file=sys.stderr)
        sys.exit(1)

    max_scale = args.scale if args.scale is not None else (1.0 if args.no_fit else None)

    mm_polylines = convert_units_to_mm(raw_polylines, args.svg_file)
    fitted = fit_to_bed(mm_polylines, max_scale=max_scale)
    segments = build_segments(fitted)

    make_static_preview(segments, args.out)
    print(f"Saved static preview: {args.out}")

    if args.animate:
        make_animated_preview(segments, args.animate_out)
        print(f"Saved animated preview: {args.animate_out}")


if __name__ == "__main__":
    main()
