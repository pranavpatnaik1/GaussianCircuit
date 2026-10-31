import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from gaussiancircuit.api.config import Settings
from gaussiancircuit.reconstruction import (
    _commands,
    _openmvs_executables,
    _ply_stats,
    analyze_navigation_mesh,
    prepare_and_run,
    queue_reconstruction,
    read_status,
)


class ReconstructionTests(unittest.TestCase):
    def test_openmvs_cpu_pipeline_uses_conservative_prototype_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tools = root / "openmvs"
            tools.mkdir()
            for executable in (
                "InterfaceCOLMAP.exe",
                "DensifyPointCloud.exe",
                "ReconstructMesh.exe",
                "TextureMesh.exe",
            ):
                (tools / executable).touch()

            openmvs = _openmvs_executables(str(tools))
            commands = _commands("colmap", root / "work", False, openmvs)

            self.assertEqual(
                [command["stage"] for command in commands],
                [
                    "feature_extraction",
                    "feature_matching",
                    "sparse_mapping",
                    "image_undistortion",
                    "scene_conversion",
                    "dense_reconstruction",
                    "mesh_generation",
                    "mesh_texturing",
                ],
            )
            dense = next(command for command in commands if command["stage"] == "dense_reconstruction")
            self.assertIn("1600", dense["args"])
            self.assertIn("4", dense["args"])

    def test_ply_stats_reads_geometry_counts(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "mesh.ply"
            path.write_bytes(
                b"ply\nformat binary_little_endian 1.0\nelement vertex 9218\n"
                b"element face 18369\nend_header\n"
            )
            self.assertEqual(_ply_stats(path), {"vertices": 9218, "faces": 18369})

    def test_navigation_analysis_finds_safe_gameplay_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "room.ply"
            vertices = [
                (x / 2, 0.0, z / 2)
                for x in range(-8, 9)
                for z in range(-6, 7)
            ]
            vertices.extend((1.5, y / 4, 1.0) for y in range(1, 8) for _ in range(4))
            lines = ["ply", "format ascii 1.0", f"element vertex {len(vertices)}", "property float x", "property float y", "property float z", "end_header"]
            lines.extend(f"{x} {y} {z}" for x, y, z in vertices)
            path.write_text("\n".join(lines) + "\n", encoding="ascii")

            navigation = analyze_navigation_mesh(path)

            self.assertGreater(navigation["coordinate_system"]["floor_inliers"], 20)
            self.assertGreaterEqual(len(navigation["checkpoints"]), 2)
            self.assertEqual(len(navigation["spawn"]), 2)
            self.assertGreater(navigation["bounds"]["half_x"], 2)

    def test_capture_is_normalized_and_colmap_pipeline_is_persisted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_id = "capture-test"
            capture_directory = root / "captures" / capture_id
            capture_directory.mkdir(parents=True)
            images = []
            for index, color in enumerate(((80, 120, 160), (150, 100, 70)), start=1):
                source = capture_directory / f"source-{index}.jpg"
                Image.new("RGB", (1280, 960), color).save(source, quality=90)
                images.append({"storage_key": f"{capture_id}/{source.name}"})
            (capture_directory / "manifest.json").write_text(
                json.dumps({"images": images}), encoding="utf-8"
            )
            settings = Settings(
                database_url=f"sqlite:///{(root / 'test.db').as_posix()}",
                capture_root=root / "captures",
                reconstruction_root=root / "reconstructions",
                public_web_url="http://testserver",
                colmap_executable="definitely-not-installed-colmap",
            )

            queue_reconstruction(settings, capture_id)
            prepare_and_run(settings, capture_id)
            status = read_status(settings, capture_id)

            self.assertEqual(status["status"], "awaiting_colmap")
            self.assertEqual(len(status["images"]), 2)
            self.assertTrue((settings.reconstruction_root / capture_id / "images" / "0001.jpg").is_file())
            pipeline = json.loads(
                (settings.reconstruction_root / capture_id / "pipeline.json").read_text(encoding="utf-8")
            )
            self.assertEqual(
                [command["stage"] for command in pipeline["commands"]],
                [
                    "feature_extraction",
                    "feature_matching",
                    "sparse_mapping",
                ],
            )


if __name__ == "__main__":
    unittest.main()
