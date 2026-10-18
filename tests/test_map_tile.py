import base64
import struct
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

import resvg_py

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "src"))

from arma3_mcp.map_tile import crop_svg, find_icon_png, find_world_svg


SVG_NS = "{http://www.w3.org/2000/svg}"


class MapTileTest(unittest.TestCase):
    def test_draws_and_clips_group_waypoint_routes(self):
        image = crop_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>',
            view_box=(20, 20, 80, 80),
            output_size=(300, 300),
            overlays=[{
                "kind": "waypoint_route",
                "name": "Alpha",
                "side": "WEST",
                "points": [[10, 60], [50, 60], [90, 60]],
                "waypoint_types": ["MOVE", "SAD"],
            }],
            world_height=100,
            show_labels=True,
        )
        root = ET.fromstring(image)
        overlay = root.find(f"{SVG_NS}g[@id='a3mcp-overlay']")
        self.assertIsNotNone(overlay)
        route_line = overlay.find(f".//{SVG_NS}polyline")
        self.assertIsNotNone(route_line)
        self.assertEqual(route_line.get("points"), "20.000,40.000 50.000,40.000 80.000,40.000")
        self.assertEqual(route_line.get("stroke"), "#2864dc")
        self.assertEqual(len(overlay.findall(f".//{SVG_NS}circle")), 1)
        self.assertEqual([text.text for text in overlay.findall(f".//{SVG_NS}text")], ["1: MOVE"])

    def test_crops_map_geometry_and_places_world_coordinate_overlays(self):
        source = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
          <defs><linearGradient id="land"><stop stop-color="#ddd"/></linearGradient></defs>
          <g id="terrain"><rect x="0" y="0" width="100" height="100" fill="url(#land)"/>
            <polygon points="10,10 90,10 90,90 10,90"/>
            <polyline points="0,50 50,50 100,50"/>
            <line x1="0" y1="0" x2="100" y2="100"/>
            <circle cx="5" cy="5" r="2"/>
          </g>
        </svg>"""
        image = crop_svg(
            source,
            view_box=(20, 20, 80, 80),
            output_size=(300, 300),
            overlays=[
                {"kind": "unit", "name": "Alpha", "side": "WEST", "pos": [50, 60]},
                {"kind": "vehicle", "name": "Truck", "side": "EAST", "pos": [70, 40]},
                {"kind": "marker", "name": "objective", "text": "OBJ", "color": "ColorRed", "pos": [50, 30]},
                {"kind": "marker", "name": "area", "color": "ColorBlue", "shape": "RECTANGLE",
                 "size": [10, 25], "pos": [15, 50]},
                {"kind": "unit", "name": "outside", "side": "EAST", "pos": [5, 5]},
            ],
            world_height=100,
            show_labels=True,
        )
        root = ET.fromstring(image)
        self.assertEqual(root.get("viewBox"), "20 20 60 60")
        self.assertEqual(root.get("width"), "300")
        self.assertEqual(root.find(f"{SVG_NS}defs") is not None, True)
        overlay = root.find(f"{SVG_NS}g[@id='a3mcp-overlay']")
        self.assertIsNotNone(overlay)
        self.assertEqual(len(overlay.findall(f"{SVG_NS}g")), 4)
        self.assertEqual(len(overlay.findall(f".//{SVG_NS}text")), 4)
        text = overlay.find(f".//{SVG_NS}text")
        self.assertEqual(text.get("font-size"), "4.000")
        self.assertEqual(text.get("font-weight"), "bold")
        unit = overlay.findall(f"{SVG_NS}g")[0]
        self.assertEqual(unit.get("transform"), "translate(50 40)")

        terrain = root.find(f"{SVG_NS}g[@id='terrain']")
        self.assertIsNotNone(terrain)
        rect = terrain.find(f"{SVG_NS}rect")
        self.assertEqual(
            (rect.get("x"), rect.get("y"), rect.get("width"), rect.get("height")),
            ("20.000", "20.000", "60.000", "60.000"),
        )
        polyline = terrain.find(f".//{SVG_NS}polyline")
        self.assertEqual(polyline.get("points"), "20.000,50.000 50.000,50.000 80.000,50.000")
        self.assertIsNone(terrain.find(f"{SVG_NS}circle"))
        area_marker = overlay.findall(f"{SVG_NS}g")[3]
        self.assertIsNotNone(area_marker.find(f"{SVG_NS}rect"))

    def test_finds_world_svg_case_insensitively(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "Stratis.svg"
            path.write_text("<svg/>", encoding="utf-8")
            self.assertEqual(find_world_svg(Path(temp), "stratis"), path)

    def test_resolves_config_paa_icons_to_png_case_insensitively(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "a3" / "air_f" / "data" / "map_icon_ca.png"
            path.parent.mkdir(parents=True)
            path.write_bytes(b"png")
            self.assertEqual(
                find_icon_png(Path(temp), r"\A3\Air_F\Data\Map_Icon_CA.PAA"),
                path,
            )
            self.assertIsNone(find_icon_png(Path(temp), r"\A3\..\outside.paa"))

    def test_crops_and_rasterizes_an_embedded_png_icon(self):
        icon_png = resvg_py.svg_to_bytes(
            svg_string=(
                '<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4">'
                '<rect width="4" height="4" fill="red"/></svg>'
            ),
            width=4,
            height=4,
        )
        image = crop_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100"/>',
            view_box=(0, 0, 100, 100),
            output_size=(100, 100),
            overlays=[
                {
                    "kind": "vehicle", "name": "Truck", "pos": [40, 50],
                    "icon_data": "data:image/png;base64," + base64.b64encode(icon_png).decode("ascii"),
                },
                {
                    "kind": "marker", "name": "objective", "shape": "ICON", "pos": [60, 50], "dir": 167,
                    "icon_data": "data:image/png;base64," + base64.b64encode(icon_png).decode("ascii"),
                },
            ],
            world_height=100,
        )
        root = ET.fromstring(image)
        icon = root.find(f".//{SVG_NS}image")
        self.assertIsNotNone(icon)
        self.assertTrue(icon.get("href", "").startswith("data:image/png;base64,"))
        self.assertEqual(len(root.findall(f".//{SVG_NS}image")), 2)
        self.assertEqual(root.findall(f".//{SVG_NS}image")[1].get("transform"), "rotate(167)")
        png = resvg_py.svg_to_bytes(svg_string=image, width=100, height=100)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")

    def test_cropped_svg_rasterizes_to_png(self):
        image = crop_svg(
            '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">'
            '<rect x="0" y="0" width="100" height="100" fill="#ddd"/></svg>',
            view_box=(10, 20, 60, 70),
            output_size=(200, 200),
            world_height=100,
        )
        png = resvg_py.svg_to_bytes(svg_string=image, width=200, height=200)
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(struct.unpack(">II", png[16:24]), (200, 200))


if __name__ == "__main__":
    unittest.main()
