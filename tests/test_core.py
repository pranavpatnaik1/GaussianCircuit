import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError
from shapely.geometry import LineString

from gaussiancircuit.cli import main
from gaussiancircuit.models import PlannerConfig, Region, Scene, Vehicle
from gaussiancircuit.planner import plan, validate_path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str) -> Scene:
    return Scene.model_validate_json((ROOT / "examples" / f"{name}.json").read_text())


class PlanningTests(unittest.TestCase):
    def test_central_obstacle_has_safe_loop(self):
        scene = load("central_table")
        vehicle, config = Vehicle(), PlannerConfig()
        result = plan(scene, vehicle, config)
        self.assertEqual(result["status"], "valid")
        points = result["track"]["centerline"]
        path = LineString([*points, points[0]])
        radius = max(vehicle.footprint_radius, config.track_width / 2) + config.safety_margin
        self.assertTrue(path.is_ring)
        self.assertGreaterEqual(path.distance(scene.floor.polygon.boundary), radius)
        for obstacle in scene.obstacles:
            self.assertGreaterEqual(path.distance(obstacle.polygon), radius)
        self.assertFalse(result["track"]["validation"]["dynamics_validated"])

    def test_unknown_region_is_blocked(self):
        scene = load("unknown_band")
        self.assertEqual(plan(scene, Vehicle(), PlannerConfig())["status"], "no_candidate_found")
        crossing = [(1, 1), (7, 1), (7, 5), (1, 5)]
        self.assertFalse(validate_path(scene, Vehicle(), PlannerConfig(), crossing)["valid"])

    def test_blocked_search_does_not_claim_global_infeasibility(self):
        result = plan(load("blocked_room"), Vehicle(), PlannerConfig())
        self.assertEqual(result["status"], "no_candidate_found")
        self.assertIsNone(result["track"])
        self.assertTrue(result["rejection_counts"])

    def test_reproducible(self):
        scene = load("central_table")
        self.assertEqual(plan(scene, Vehicle(), PlannerConfig()), plan(scene, Vehicle(), PlannerConfig()))

    def test_connecting_edges_are_validated(self):
        scene = load("central_table")
        crossing = [(1, 1), (7, 5), (7, 1), (1, 5)]
        self.assertFalse(validate_path(scene, Vehicle(), PlannerConfig(), crossing)["valid"])

    def test_cli_exports_input_report_and_preview(self):
        with tempfile.TemporaryDirectory() as directory:
            code = main([str(ROOT / "examples/central_table.json"), "--output", directory])
            self.assertEqual(code, 0)
            result = json.loads((Path(directory) / "report.json").read_text())
            self.assertEqual(result["scene_revision"], 1)
            self.assertTrue((Path(directory) / "scene.json").exists())
            self.assertIn("<svg", (Path(directory) / "preview.svg").read_text())

    def test_invalid_geometry_and_nonfinite_values(self):
        with self.assertRaises(ValidationError):
            Region(id="bowtie", vertices=[(0, 0), (2, 2), (0, 2), (2, 0)])
        with self.assertRaises(ValidationError):
            Vehicle(width=float("nan"))
        with self.assertRaises(ValidationError):
            Vehicle(wheelbase=2)

    def test_scene_regions_must_fit_and_have_unique_ids(self):
        data = load("central_table").model_dump()
        data["obstacles"][0]["id"] = data["floor"]["id"]
        with self.assertRaises(ValidationError):
            Scene.model_validate(data)
        data = load("central_table").model_dump()
        data["obstacles"][0]["vertices"][0] = (-1, -1)
        with self.assertRaises(ValidationError):
            Scene.model_validate(data)

    def test_track_cannot_be_narrower_than_vehicle(self):
        with self.assertRaises(ValueError):
            plan(load("central_table"), Vehicle(), PlannerConfig(track_width=0.1))


if __name__ == "__main__":
    unittest.main()
