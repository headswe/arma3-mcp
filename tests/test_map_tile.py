import struct
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from tempfile import TemporaryDirectory

import resvg_py

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "src"))

from arma3_mcp.map_tile import crop_svg, find_world_svg


SVG_NS = "{http://www.w3.org/2000/svg}"


class MapTileTest(unittest.TestCase):
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
