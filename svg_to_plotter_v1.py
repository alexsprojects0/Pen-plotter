#!/usr/bin/env python3
"""
Converts SVG line art into the plotter's serial protocol (G1/M3/M5/M2)
and streams it to the Arduino over USB.

Usage:
    python svg_to_plotter.py drawing.svg --port COM5
    python svg_to_plotter.py drawing.svg --dry-run
"""

import argparse
import math
import re
import sys
import time
import xml.etree.ElementTree as ET

BED_X_MM = 105.0
BED_Y_MM = 166.0
MARGIN_MM = 5.0

CURVE_SEGMENTS = 20

TOKEN_RE = re.compile(r"[MmLlHhVvCcSsQqTtAaZz]|-?\d*\.?\d+(?:[eE][-+]?\d+)?")


def parse_path_d(d):
    tokens = TOKEN_RE.findall(d)
    i = 0

    def next_num():
        nonlocal i
        val = float(tokens[i])
        i += 1
        return val

    subpaths = []
    current = []
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    last_ctrl = None
    cmd = None

    while i < len(tokens):
        tok = tokens[i]
        if re.match(r"[A-Za-z]", tok):
            cmd = tok
            i += 1

        if cmd in ("M", "m"):
            x, y = next_num(), next_num()
            if cmd == "m" and current:
                x += cur[0]; y += cur[1]
            if current:
                subpaths.append(current)
            current = [(x, y)]
            cur = (x, y)
            start = cur
            last_ctrl = None
            cmd = "L" if cmd == "M" else "l"

        elif cmd in ("L", "l"):
            x, y = next_num(), next_num()
            if cmd == "l":
                x += cur[0]; y += cur[1]
            current.append((x, y))
            cur = (x, y)
            last_ctrl = None

        elif cmd in ("H", "h"):
            x = next_num()
            if cmd == "h":
                x += cur[0]
            current.append((x, cur[1]))
            cur = (x, cur[1])
            last_ctrl = None

        elif cmd in ("V", "v"):
            y = next_num()
            if cmd == "v":
                y += cur[1]
            current.append((cur[0], y))
            cur = (cur[0], y)
            last_ctrl = None

        elif cmd in ("C", "c"):
            x1, y1, x2, y2, x, y = (next_num() for _ in range(6))
            if cmd == "c":
                x1 += cur[0]; y1 += cur[1]
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            current.extend(_cubic_bezier(cur, (x1, y1), (x2, y2), (x, y)))
            cur = (x, y)
            last_ctrl = (x2, y2)

        elif cmd in ("S", "s"):
            x2, y2, x, y = (next_num() for _ in range(4))
            if cmd == "s":
                x2 += cur[0]; y2 += cur[1]
                x += cur[0]; y += cur[1]
            if last_ctrl:
                x1 = 2 * cur[0] - last_ctrl[0]
                y1 = 2 * cur[1] - last_ctrl[1]
            else:
                x1, y1 = cur
            current.extend(_cubic_bezier(cur, (x1, y1), (x2, y2), (x, y)))
            cur = (x, y)
            last_ctrl = (x2, y2)

        elif cmd in ("Q", "q"):
            x1, y1, x, y = (next_num() for _ in range(4))
            if cmd == "q":
                x1 += cur[0]; y1 += cur[1]
                x += cur[0]; y += cur[1]
            current.extend(_quad_bezier(cur, (x1, y1), (x, y)))
            cur = (x, y)
            last_ctrl = (x1, y1)

        elif cmd in ("T", "t"):
            x, y = next_num(), next_num()
            if cmd == "t":
                x += cur[0]; y += cur[1]
            if last_ctrl:
                x1 = 2 * cur[0] - last_ctrl[0]
                y1 = 2 * cur[1] - last_ctrl[1]
            else:
                x1, y1 = cur
            current.extend(_quad_bezier(cur, (x1, y1), (x, y)))
            cur = (x, y)
            last_ctrl = (x1, y1)

        elif cmd in ("A", "a"):
            rx, ry, rot, large, sweep, x, y = (next_num() for _ in range(7))
            if cmd == "a":
                x += cur[0]; y += cur[1]
            current.append((x, y))
            cur = (x, y)
            last_ctrl = None

        elif cmd in ("Z", "z"):
            current.append(start)
            cur = start
            subpaths.append(current)
            current = []
            last_ctrl = None

        else:
            i += 1

    if current:
        subpaths.append(current)
    return subpaths


def _cubic_bezier(p0, p1, p2, p3, n=CURVE_SEGMENTS):
    pts = []
    for k in range(1, n + 1):
        t = k / n
        mt = 1 - t
        x = (mt**3 * p0[0] + 3 * mt**2 * t * p1[0] +
             3 * mt * t**2 * p2[0] + t**3 * p3[0])
        y = (mt**3 * p0[1] + 3 * mt**2 * t * p1[1] +
             3 * mt * t**2 * p2[1] + t**3 * p3[1])
        pts.append((x, y))
    return pts


def _quad_bezier(p0, p1, p2, n=CURVE_SEGMENTS):
    pts = []
    for k in range(1, n + 1):
        t = k / n
        mt = 1 - t
        x = mt**2 * p0[0] + 2 * mt * t * p1[0] + t**2 * p2[0]
        y = mt**2 * p0[1] + 2 * mt * t * p1[1] + t**2 * p2[1]
        pts.append((x, y))
    return pts


def _strip_ns(tag):
    return tag.split("}")[-1] if "}" in tag else tag


IDENTITY = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)


def _compose(m1, m2):
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def _apply(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def parse_transform(s):
    combined = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        nums = [float(v) for v in
                re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", args)]
        if name == "matrix" and len(nums) == 6:
            m = tuple(nums)
        elif name == "translate":
            tx = nums[0] if nums else 0.0
            ty = nums[1] if len(nums) > 1 else 0.0
            m = (1.0, 0.0, 0.0, 1.0, tx, ty)
        elif name == "scale":
            sx = nums[0] if nums else 1.0
            sy = nums[1] if len(nums) > 1 else sx
            m = (sx, 0.0, 0.0, sy, 0.0, 0.0)
        elif name == "rotate" and nums:
            ang = math.radians(nums[0])
            cos_a, sin_a = math.cos(ang), math.sin(ang)
            rot = (cos_a, sin_a, -sin_a, cos_a, 0.0, 0.0)
            if len(nums) >= 3:
                cx, cy = nums[1], nums[2]
                m = _compose(_compose((1, 0, 0, 1, cx, cy), rot),
                             (1, 0, 0, 1, -cx, -cy))
            else:
                m = rot
        else:
            continue
        combined = _compose(combined, m)
    return combined


def load_svg_as_polylines(svg_path):
    tree = ET.parse(svg_path)
    root = tree.getroot()
    polylines = []
    _walk(root, IDENTITY, polylines)
    return polylines


def _walk(el, parent_m, polylines):
    local = el.get("transform")
    m = _compose(parent_m, parse_transform(local)) if local else parent_m

    tag = _strip_ns(el.tag)

    if tag == "path":
        d = el.get("d")
        if d:
            for sub in parse_path_d(d):
                polylines.append([_apply(m, x, y) for x, y in sub])

    elif tag == "line":
        x1, y1 = float(el.get("x1", 0)), float(el.get("y1", 0))
        x2, y2 = float(el.get("x2", 0)), float(el.get("y2", 0))
        polylines.append([_apply(m, x1, y1), _apply(m, x2, y2)])

    elif tag in ("polyline", "polygon"):
        pts_str = el.get("points", "")
        coords = re.findall(r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", pts_str)
        pts = [(float(coords[i]), float(coords[i + 1]))
               for i in range(0, len(coords) - 1, 2)]
        if tag == "polygon" and pts:
            pts.append(pts[0])
        if pts:
            polylines.append([_apply(m, x, y) for x, y in pts])

    for child in el:
        _walk(child, m, polylines)


def _parse_length(s):
    match = re.match(r"\s*(-?\d*\.?\d+(?:[eE][-+]?\d+)?)\s*([a-zA-Z%]*)", s or "")
    if not match:
        return (0.0, "")
    return (float(match.group(1)), match.group(2).lower())


def get_mm_per_unit(svg_path):
    unit_to_mm = {"mm": 1.0, "cm": 10.0, "in": 25.4, "px": 25.4 / 96,
                  "pt": 25.4 / 72, "pc": 25.4 / 6, "": 25.4 / 96}

    root = ET.parse(svg_path).getroot()
    width_attr = root.get("width")
    viewbox_attr = root.get("viewBox")

    if viewbox_attr:
        vb = [float(v) for v in re.findall(
            r"-?\d*\.?\d+(?:[eE][-+]?\d+)?", viewbox_attr)]
        vb_w = vb[2] if len(vb) == 4 and vb[2] else None
        if width_attr and vb_w:
            width_val, unit = _parse_length(width_attr)
            width_mm = width_val * unit_to_mm.get(unit, unit_to_mm[""])
            return width_mm / vb_w
        return 1.0

    if width_attr:
        _, unit = _parse_length(width_attr)
        return unit_to_mm.get(unit, unit_to_mm[""])

    return unit_to_mm[""]


def convert_units_to_mm(polylines, svg_path):
    mm_per_unit = get_mm_per_unit(svg_path)
    return [[(x * mm_per_unit, y * mm_per_unit) for x, y in pl] for pl in polylines]


def fit_to_bed(polylines, max_scale=None):
    all_x = [x for pl in polylines for x, y in pl]
    all_y = [y for pl in polylines for x, y in pl]
    if not all_x:
        return polylines

    min_x, max_x = min(all_x), max(all_x)
    min_y, max_y = min(all_y), max(all_y)
    art_w = max(max_x - min_x, 1e-6)
    art_h = max(max_y - min_y, 1e-6)

    avail_w = BED_X_MM - 2 * MARGIN_MM
    avail_h = BED_Y_MM - 2 * MARGIN_MM

    scale = min(avail_w / art_w, avail_h / art_h)
    if max_scale is not None:
        scale = min(scale, max_scale)

    off_x = MARGIN_MM + (avail_w - art_w * scale) / 2 - min_x * scale
    off_y = MARGIN_MM + (avail_h - art_h * scale) / 2 - min_y * scale

    fitted = []
    for pl in polylines:
        fitted.append([(x * scale + off_x, y * scale + off_y) for x, y in pl])
    return fitted


def polylines_to_commands(polylines):
    commands = ["M5"]
    for pl in polylines:
        if not pl:
            continue
        x0, y0 = pl[0]
        commands.append(f"G1 X{x0:.2f} Y{y0:.2f}")
        commands.append("M3")
        for x, y in pl[1:]:
            commands.append(f"G1 X{x:.2f} Y{y:.2f}")
        commands.append("M5")
    commands.append("M2")
    return commands


_XY_STEPS_PER_MM = 2048.0
_XY_MAX_SPEED = 350.0
_Z_MAX_SPEED = 300.0
_Z_LIFT_STEPS = 8192


def _estimate_seconds(cmd, state):
    if cmd.startswith("G1"):
        x_idx, y_idx = cmd.find("X"), cmd.find("Y")
        new_x = float(cmd[x_idx + 1:y_idx].strip()) if x_idx != -1 else state["x"]
        new_y = float(cmd[y_idx + 1:].strip()) if y_idx != -1 else state["y"]
        dx_steps = abs(new_x - state["x"]) * _XY_STEPS_PER_MM
        dy_steps = abs(new_y - state["y"]) * _XY_STEPS_PER_MM
        state["x"], state["y"] = new_x, new_y
        return max(dx_steps, dy_steps) / _XY_MAX_SPEED
    elif cmd in ("M3", "M5"):
        return _Z_LIFT_STEPS / _Z_MAX_SPEED
    elif cmd == "M2":
        dx_steps = abs(state["x"]) * _XY_STEPS_PER_MM
        dy_steps = abs(state["y"]) * _XY_STEPS_PER_MM
        state["x"], state["y"] = 0.0, 0.0
        return _Z_LIFT_STEPS / _Z_MAX_SPEED + max(dx_steps, dy_steps) / _XY_MAX_SPEED
    return 5.0


def _drain_boot_messages(ser, max_seconds):
    end_time = time.time() + max_seconds
    while time.time() < end_time:
        line = ser.readline().decode(errors="ignore").strip()
        if line:
            print(f"  (arduino: {line})")
            if line.lower() == "ok":
                return


def _wait_for_ok(ser, max_seconds):
    end_time = time.time() + max_seconds
    while time.time() < end_time:
        line = ser.readline().decode(errors="ignore").strip()
        if not line:
            continue
        if line.lower() == "ok":
            return True
        print(f"  (arduino: {line})")
    return False


def stream_to_arduino(commands, port, baud=9600):
    import serial

    ser = serial.Serial(port, baud, timeout=2)
    time.sleep(2)

    print("Waiting for Arduino to boot...")
    _drain_boot_messages(ser, max_seconds=6)

    state = {"x": 0.0, "y": 0.0}
    total_est = sum(_estimate_seconds(c, state) for c in commands)
    state = {"x": 0.0, "y": 0.0}
    print(f"Estimated total plot time: ~{total_est/60:.1f} minutes "
          f"({len(commands)} commands).")

    for i, cmd in enumerate(commands):
        ser.write((cmd + "\n").encode())
        est = _estimate_seconds(cmd, state)
        timeout = max(20.0, est * 1.5 + 5.0)
        print(f"[{i+1}/{len(commands)}] -> {cmd}  (~{est:.1f}s, waiting up to {timeout:.0f}s)")
        if not _wait_for_ok(ser, max_seconds=timeout):
            print(f"  No 'ok' received within {timeout:.0f}s. Stopping.")
            print(f"  Check the Arduino is powered, wired correctly, and that")
            print(f"  no other program has the serial port open.")
            ser.close()
            return

    ser.close()
    print("Done.")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("svg_file", help="Path to SVG line art")
    ap.add_argument("--port", help="Serial port, e.g. COM5 or /dev/ttyUSB0")
    ap.add_argument("--baud", type=int, default=9600)
    ap.add_argument("--dry-run", action="store_true",
                     help="Print commands instead of sending them")
    ap.add_argument("--no-fit", action="store_true",
                     help="Keep artwork at its actual size instead of "
                          "stretching it to fill the bed")
    ap.add_argument("--scale", type=float, default=None,
                     help="Cap the auto-fit scale factor manually")
    args = ap.parse_args()

    raw_polylines = load_svg_as_polylines(args.svg_file)
    if not raw_polylines:
        print("No drawable lines/paths found in that SVG.", file=sys.stderr)
        sys.exit(1)

    max_scale = args.scale if args.scale is not None else (1.0 if args.no_fit else None)

    mm_polylines = convert_units_to_mm(raw_polylines, args.svg_file)
    fitted = fit_to_bed(mm_polylines, max_scale=max_scale)
    commands = polylines_to_commands(fitted)

    if args.dry_run or not args.port:
        for c in commands:
            print(c)
        if not args.dry_run:
            print("\n(no --port given, so nothing was sent -- use --port COMx)",
                  file=sys.stderr)
        return

    stream_to_arduino(commands, args.port, args.baud)


if __name__ == "__main__":
    main()
