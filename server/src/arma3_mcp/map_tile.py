"""Crop exported Arma terrain SVGs and add live tactical overlays."""

from __future__ import annotations

import math
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

_NUMBER = re.compile(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?")
_SIDES = {
    "WEST": "#2864dc",
    "EAST": "#d83232",
    "GUER": "#24934b",
    "CIV": "#a343b6",
}
_MARKER_COLORS = {
    "COLORRED": "#d83232",
    "COLORBLUE": "#2864dc",
    "COLORGREEN": "#24934b",
    "COLORBLACK": "#242424",
    "COLORWHITE": "#ffffff",
    "COLORYELLOW": "#e3b52d",
    "COLORORANGE": "#ed7d24",
    "COLORPINK": "#d86a9f",
    "COLORPURPLE": "#784bb5",
    "COLORBLUFOR": "#2864dc",
    "COLOROPFOR": "#d83232",
    "COLORINDEPENDENT": "#24934b",
    "COLORCIVILIAN": "#a343b6",
    "COLORWEST": "#2864dc",
    "COLOREAST": "#d83232",
    "COLORGUER": "#24934b",
    "COLORCIV": "#a343b6",
}


def _tag(element: ET.Element) -> str:
    return element.tag.rsplit("}", 1)[-1]


def _number(value: str | None, default: float = 0.0) -> float:
    if value is None:
        return default
    match = _NUMBER.search(value)
    return float(match.group()) if match else default


def svg_extent(root: ET.Element) -> tuple[float, float]:
    """Return the coordinate-space width and height of an exported terrain SVG."""
    view_box = root.get("viewBox")
    if view_box:
        values = [_number(value) for value in _NUMBER.findall(view_box)]
        if len(values) == 4 and values[2] > 0 and values[3] > 0:
            return values[2], values[3]
    width, height = _number(root.get("width")), _number(root.get("height"))
    if width <= 0 or height <= 0:
        raise ValueError("terrain SVG must have a positive width and height or viewBox")
    return width, height


def _parse_points(value: str) -> list[tuple[float, float]]:
    values = [float(item) for item in _NUMBER.findall(value)]
    if len(values) % 2:
        raise ValueError("terrain SVG contains an invalid point list")
    return list(zip(values[::2], values[1::2]))


def _format_points(points: Iterable[tuple[float, float]]) -> str:
    return " ".join(f"{x:.3f},{y:.3f}" for x, y in points)


def _inside(x: float, y: float, bounds: tuple[float, float, float, float]) -> bool:
    left, top, right, bottom = bounds
    return left <= x <= right and top <= y <= bottom


def _clip_segment(
    start: tuple[float, float],
    end: tuple[float, float],
    bounds: tuple[float, float, float, float],
) -> tuple[tuple[float, float], tuple[float, float]] | None:
    left, top, right, bottom = bounds
    x0, y0 = start
    x1, y1 = end
    dx, dy = x1 - x0, y1 - y0
    low, high = 0.0, 1.0
    for p, q in ((-dx, x0 - left), (dx, right - x0), (-dy, y0 - top), (dy, bottom - y0)):
        if p == 0:
            if q < 0:
                return None
            continue
        ratio = q / p
        if p < 0:
            low = max(low, ratio)
        else:
            high = min(high, ratio)
        if low > high:
            return None
    return (x0 + low * dx, y0 + low * dy), (x0 + high * dx, y0 + high * dy)


def _clip_polygon(
    points: list[tuple[float, float]],
    bounds: tuple[float, float, float, float],
) -> list[tuple[float, float]]:
    left, top, right, bottom = bounds
    for inside, intersect in (
        (lambda p: p[0] >= left, lambda a, b: (left, a[1] + (b[1] - a[1]) * (left - a[0]) / (b[0] - a[0]))),
        (lambda p: p[0] <= right, lambda a, b: (right, a[1] + (b[1] - a[1]) * (right - a[0]) / (b[0] - a[0]))),
        (lambda p: p[1] >= top, lambda a, b: (a[0] + (b[0] - a[0]) * (top - a[1]) / (b[1] - a[1]), top)),
        (lambda p: p[1] <= bottom, lambda a, b: (a[0] + (b[0] - a[0]) * (bottom - a[1]) / (b[1] - a[1]), bottom)),
    ):
        if not points:
            break
        output = []
        previous = points[-1]
        for current in points:
            previous_inside, current_inside = inside(previous), inside(current)
            if current_inside:
                if not previous_inside:
                    output.append(intersect(previous, current))
                output.append(current)
            elif previous_inside:
                output.append(intersect(previous, current))
            previous = current
        points = output
    return points


def _clip_polyline(
    points: list[tuple[float, float]],
    bounds: tuple[float, float, float, float],
) -> list[list[tuple[float, float]]]:
    paths: list[list[tuple[float, float]]] = []
    for start, end in zip(points, points[1:]):
        clipped = _clip_segment(start, end, bounds)
        if clipped is None:
            continue
        a, b = clipped
        if paths and math.isclose(paths[-1][-1][0], a[0]) and math.isclose(paths[-1][-1][1], a[1]):
            paths[-1].append(b)
        else:
            paths.append([a, b])
    return paths


def _clone_clipped(
    element: ET.Element,
    bounds: tuple[float, float, float, float],
) -> ET.Element | None:
    tag = _tag(element)
    if tag == "g":
        clone = ET.Element(element.tag, element.attrib)
        for child in element:
            cropped = _clone_clipped(child, bounds)
            if cropped is not None:
                clone.append(cropped)
        return clone if len(clone) else None

    if tag == "polygon":
        points = _clip_polygon(_parse_points(element.get("points", "")), bounds)
        if len(points) < 3:
            return None
        clone = ET.Element(element.tag, element.attrib)
        clone.set("points", _format_points(points))
        return clone

    if tag == "polyline":
        points = _parse_points(element.get("points", ""))
        paths = _clip_polyline(points, bounds)
        if not paths:
            return None
        parent = ET.Element(f"{{{SVG_NS}}}g")
        for path in paths:
            clone = ET.Element(element.tag, element.attrib)
            clone.set("points", _format_points(path))
            parent.append(clone)
        return parent

    if tag == "line":
        clipped = _clip_segment(
            (_number(element.get("x1")), _number(element.get("y1"))),
            (_number(element.get("x2")), _number(element.get("y2"))),
            bounds,
        )
        if clipped is None:
            return None
        (x1, y1), (x2, y2) = clipped
        clone = ET.Element(element.tag, element.attrib)
        clone.attrib.update(x1=f"{x1:.3f}", y1=f"{y1:.3f}", x2=f"{x2:.3f}", y2=f"{y2:.3f}")
        return clone

    if tag == "rect":
        x, y = _number(element.get("x")), _number(element.get("y"))
        right, bottom = x + _number(element.get("width")), y + _number(element.get("height"))
        left_bound, top_bound, right_bound, bottom_bound = bounds
        x1, y1 = max(x, left_bound), max(y, top_bound)
        x2, y2 = min(right, right_bound), min(bottom, bottom_bound)
        if x1 >= x2 or y1 >= y2:
            return None
        clone = ET.Element(element.tag, element.attrib)
        clone.attrib.update(x=f"{x1:.3f}", y=f"{y1:.3f}", width=f"{x2 - x1:.3f}", height=f"{y2 - y1:.3f}")
        return clone

    if tag == "text":
        if not _inside(_number(element.get("x")), _number(element.get("y")), bounds):
            return None
        return ET.fromstring(ET.tostring(element, encoding="unicode"))

    if tag in ("circle", "ellipse"):
        cx, cy = _number(element.get("cx")), _number(element.get("cy"))
        rx = _number(element.get("r")) if tag == "circle" else _number(element.get("rx"))
        ry = rx if tag == "circle" else _number(element.get("ry"))
        left, top, right, bottom = bounds
        if cx + rx < left or cx - rx > right or cy + ry < top or cy - ry > bottom:
            return None
        return ET.fromstring(ET.tostring(element, encoding="unicode"))

    return None


def crop_svg(
    source: str,
    *,
    view_box: tuple[float, float, float, float],
    output_size: tuple[int, int],
    overlays: Sequence[Mapping[str, Any]] = (),
    world_height: float,
    show_labels: bool = False,
) -> str:
    """Clip an exported full-world SVG and add point overlays in Arma world coordinates."""
    root = ET.fromstring(source)
    width, height = svg_extent(root)
    left, top, right, bottom = view_box
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError(f"requested crop must fit inside the SVG extent {width:g} x {height:g}")

    out_width, out_height = output_size
    result = ET.Element(
        f"{{{SVG_NS}}}svg",
        {"width": str(out_width), "height": str(out_height),
         "viewBox": f"{left:g} {top:g} {right - left:g} {bottom - top:g}"},
    )
    clip = (left, top, right, bottom)
    for child in root:
        if _tag(child) == "defs":
            result.append(ET.fromstring(ET.tostring(child, encoding="unicode")))
        else:
            cropped = _clone_clipped(child, clip)
            if cropped is not None:
                result.append(cropped)

    overlay = ET.SubElement(result, f"{{{SVG_NS}}}g", {"id": "a3mcp-overlay"})
    meters_per_pixel = max((right - left) / out_width, (bottom - top) / out_height)
    radius = max(3.0, meters_per_pixel * 5.0)

    def add_label(group: ET.Element, label: str) -> None:
        if show_labels and label:
            text = ET.SubElement(group, f"{{{SVG_NS}}}text", {
                "x": f"{radius * 1.5:.3f}", "y": f"{radius * 0.4:.3f}",
                "fill": "#111111", "stroke": "#ffffff", "stroke-width": f"{meters_per_pixel * 3:.3f}",
                "paint-order": "stroke", "font-size": f"{meters_per_pixel * 20:.3f}",
                "font-weight": "bold",
            })
            text.text = label

    def add_icon(group: ET.Element, item: Mapping[str, Any]) -> bool:
        icon_data = item.get("icon_data")
        if not isinstance(icon_data, str) or not icon_data.startswith("data:image/png;base64,"):
            return False
        attributes = {
            "x": f"{-radius:g}", "y": f"{-radius:g}",
            "width": f"{radius * 2:g}", "height": f"{radius * 2:g}",
            "href": icon_data, "preserveAspectRatio": "xMidYMid meet",
        }
        if item.get("kind") == "marker":
            direction = _number(str(item.get("dir", 0)))
            if direction:
                attributes["transform"] = f"rotate({direction:g})"
        ET.SubElement(group, f"{{{SVG_NS}}}image", attributes)
        return True

    for item in overlays:
        position = item.get("pos", [])
        if len(position) < 2:
            continue
        x, world_y = float(position[0]), float(position[1])
        y = world_height - world_y
        kind = str(item.get("kind", "unit"))
        marker_shape = str(item.get("shape", "")).upper()
        marker_size = item.get("size", [])
        intersects = _inside(x, y, clip)
        if kind == "marker" and marker_shape in ("RECTANGLE", "ELLIPSE") and len(marker_size) >= 2:
            half_width, half_height = abs(float(marker_size[0])), abs(float(marker_size[1]))
            extent = math.hypot(half_width, half_height)
            left, top, right, bottom = clip
            intersects = x + extent >= left and x - extent <= right
            intersects = intersects and y + extent >= top and y - extent <= bottom
        if not intersects:
            continue
        side = str(item.get("side", "")).upper()
        color = _SIDES.get(side, "#555555")
        label = str(item.get("name", ""))
        group = ET.SubElement(overlay, f"{{{SVG_NS}}}g", {"transform": f"translate({x:g} {y:g})"})
        title = ET.SubElement(group, f"{{{SVG_NS}}}title")
        if kind == "marker":
            label = str(item.get("text") or label)
            marker_color = str(item.get("color", "")).upper()
            color = _MARKER_COLORS.get(marker_color, "#555555")
            title.text = f"marker: {label}" if label else "map marker"
            if marker_shape in ("RECTANGLE", "ELLIPSE") and len(marker_size) >= 2:
                half_width, half_height = abs(float(marker_size[0])), abs(float(marker_size[1]))
                tag = "rect" if marker_shape == "RECTANGLE" else "ellipse"
                shape_attrs = (
                    {"x": f"{-half_width:g}", "y": f"{-half_height:g}",
                     "width": f"{half_width * 2:g}", "height": f"{half_height * 2:g}"}
                    if tag == "rect"
                    else {"cx": "0", "cy": "0", "rx": f"{half_width:g}", "ry": f"{half_height:g}"}
                )
                shape_attrs.update({
                    "fill": color, "fill-opacity": "0.18", "stroke": color,
                    "stroke-width": f"{meters_per_pixel * 1.5:.3f}",
                    "transform": f"rotate({-_number(str(item.get('dir', 0))):g})",
                })
                ET.SubElement(group, f"{{{SVG_NS}}}{tag}", shape_attrs)
                add_label(group, label)
                continue
            if add_icon(group, item):
                add_label(group, label)
                continue
            ET.SubElement(group, f"{{{SVG_NS}}}path", {
                "d": f"M 0 {-radius:g} L {radius:g} 0 L 0 {radius:g} L {-radius:g} 0 Z",
                "fill": color, "stroke": "#ffffff", "stroke-width": f"{meters_per_pixel * 1.5:.3f}",
            })
            add_label(group, label)
            continue
        title.text = f"{kind}: {label} ({side})" if label else f"{kind} ({side})"
        if add_icon(group, item):
            add_label(group, label)
            continue
        shape = "rect" if kind == "vehicle" else "circle"
        if shape == "rect":
            ET.SubElement(group, f"{{{SVG_NS}}}rect", {
                "x": f"{-radius:.3f}", "y": f"{-radius:.3f}",
                "width": f"{radius * 2:.3f}", "height": f"{radius * 2:.3f}",
                "fill": color, "stroke": "#ffffff", "stroke-width": f"{meters_per_pixel * 1.5:.3f}",
                "transform": "rotate(45)",
            })
        else:
            if not item.get("alive", True):
                color = "#8a8d84"
            ET.SubElement(group, f"{{{SVG_NS}}}circle", {
                "cx": "0", "cy": "0", "r": f"{radius:.3f}",
                "fill": color, "stroke": "#ffffff", "stroke-width": f"{meters_per_pixel * 1.5:.3f}",
            })
        add_label(group, label)

    return ET.tostring(result, encoding="unicode")


def find_icon_png(directory: Path, icon: str) -> Path | None:
    """Resolve an Arma config icon path (PAA) to its extracted PNG, case-insensitively."""
    normalized = icon.replace("\\", "/").strip("/")
    parts = normalized.split("/")
    if not normalized or any(part in ("", ".", "..") for part in parts):
        return None
    parts[-1] = re.sub(r"\.paa$", ".png", parts[-1], flags=re.IGNORECASE)
    if not parts[-1].casefold().endswith(".png"):
        return None

    path = directory.joinpath(*parts)
    if path.is_file():
        return path
    current = directory
    for part in parts:
        if not current.is_dir():
            return None
        match = next((entry for entry in current.iterdir() if entry.name.casefold() == part.casefold()), None)
        if match is None:
            return None
        current = match
    return current if current.is_file() else None


def find_world_svg(directory: Path, world: str) -> Path:
    """Find the exported terrain whose filename matches the active world."""
    candidate = directory / f"{world}.svg"
    if candidate.is_file():
        return candidate
    if directory.is_dir():
        wanted = world.casefold()
        for path in directory.glob("*.svg"):
            if path.stem.casefold() == wanted:
                return path
    raise FileNotFoundError(f"no exported terrain SVG for world {world!r} in {directory}")
