"""Prepare captured photos and drive the COLMAP room-reconstruction pipeline."""

from __future__ import annotations

import hashlib
import json
import math
import re
import shutil
import struct
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageFilter, ImageOps, ImageStat
from pillow_heif import register_heif_opener

from .api.config import Settings

register_heif_opener(thumbnails=False)


class ReconstructionQualityError(RuntimeError):
    """The tools ran, but the capture cannot produce a usable room."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def status_path(settings: Settings, capture_id: str) -> Path:
    return settings.reconstruction_root / capture_id / "status.json"


def read_status(settings: Settings, capture_id: str) -> dict[str, Any] | None:
    path = status_path(settings, capture_id)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def queue_reconstruction(settings: Settings, capture_id: str) -> dict[str, Any]:
    existing = read_status(settings, capture_id)
    if existing and existing["status"] not in {"failed", "cancelled"}:
        return existing
    payload = {
        "schema_version": "1.0",
        "capture_id": capture_id,
        "status": "queued",
        "stage": "capture_complete",
        "progress": 0,
        "created_at": _now(),
        "updated_at": _now(),
        "images": [],
        "warnings": [],
        "error": None,
    }
    _atomic_json(status_path(settings, capture_id), payload)
    return payload


def _source_path(settings: Settings, storage_key: str) -> Path:
    root = settings.capture_root.resolve()
    source = (root / storage_key).resolve()
    if not source.is_relative_to(root):
        raise ValueError("Capture manifest contains an unsafe storage path")
    if not source.is_file():
        raise FileNotFoundError(f"Capture image is missing: {storage_key}")
    return source


def _prepare_image(source: Path, destination: Path) -> dict[str, Any]:
    with Image.open(source) as opened:
        opened.load()
        oriented = ImageOps.exif_transpose(opened)
        rgb = oriented.convert("RGB")
        width, height = rgb.size
        sample = ImageOps.grayscale(rgb.copy())
        sample.thumbnail((512, 512))
        brightness = round(ImageStat.Stat(sample).mean[0], 2)
        edge_variance = round(ImageStat.Stat(sample.filter(ImageFilter.FIND_EDGES)).var[0], 2)
        destination.parent.mkdir(parents=True, exist_ok=True)
        rgb.save(destination, format="JPEG", quality=94, subsampling=0, optimize=True)
    warnings: list[str] = []
    if min(width, height) < 720:
        warnings.append("low_resolution")
    if brightness < 22:
        warnings.append("too_dark")
    elif brightness > 238:
        warnings.append("overexposed")
    if edge_variance < 18:
        warnings.append("possible_blur_or_low_texture")
    return {
        "file": destination.name,
        "width": width,
        "height": height,
        "brightness": brightness,
        "edge_variance": edge_variance,
        "warnings": warnings,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
    }


def _openmvs_executables(directory: str) -> dict[str, str] | None:
    if not directory:
        return None
    root = Path(directory)
    names = {
        "interface": "InterfaceCOLMAP.exe",
        "densify": "DensifyPointCloud.exe",
        "mesh": "ReconstructMesh.exe",
        "texture": "TextureMesh.exe",
    }
    executables = {name: str(root / filename) for name, filename in names.items()}
    return executables if all(Path(path).is_file() for path in executables.values()) else None


def _commands(
    executable: str,
    workspace: Path,
    colmap_dense_available: bool,
    openmvs: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    database = workspace / "database.db"
    images = workspace / "images"
    sparse = workspace / "sparse"
    dense = workspace / "dense"
    openmvs_workspace = workspace / "openmvs"
    commands = [
        {"stage": "feature_extraction", "args": [executable, "feature_extractor", "--database_path", str(database), "--image_path", str(images), "--ImageReader.single_camera", "1", "--ImageReader.camera_model", "OPENCV", "--FeatureExtraction.use_gpu", "0", "--FeatureExtraction.num_threads", "2", "--FeatureExtraction.max_image_size", "2000", "--SiftExtraction.first_octave", "0"]},
        {"stage": "feature_matching", "args": [executable, "sequential_matcher", "--database_path", str(database), "--FeatureMatching.use_gpu", "0", "--FeatureMatching.num_threads", "2", "--SiftMatching.cpu_brute_force_matcher", "1"]},
        {"stage": "sparse_mapping", "args": [executable, "mapper", "--database_path", str(database), "--image_path", str(images), "--output_path", str(sparse)]},
    ]
    if openmvs:
        commands.extend([
            {"stage": "image_undistortion", "engine": "colmap", "args": [executable, "image_undistorter", "--image_path", str(images), "--input_path", str(sparse / "selected"), "--output_path", str(openmvs_workspace), "--output_type", "COLMAP", "--max_image_size", "1600"]},
            {"stage": "scene_conversion", "engine": "openmvs", "args": [openmvs["interface"], "-i", str(openmvs_workspace), "-o", str(openmvs_workspace / "scene.mvs"), "--image-folder", str(openmvs_workspace / "images"), "--max-threads", "4"]},
            {"stage": "dense_reconstruction", "engine": "openmvs", "args": [openmvs["densify"], "-i", str(openmvs_workspace / "scene.mvs"), "-o", str(openmvs_workspace / "scene_dense.mvs"), "--resolution-level", "2", "--max-resolution", "1600", "--min-resolution", "640", "--number-views", "3", "--iters", "2", "--geometric-iters", "1", "--max-threads", "4", "--estimate-colors", "1", "--estimate-normals", "1"]},
            {"stage": "mesh_generation", "engine": "openmvs", "args": [openmvs["mesh"], "-i", str(openmvs_workspace / "scene_dense.mvs"), "-o", str(openmvs_workspace / "scene_mesh.mvs"), "--max-threads", "4"]},
            {"stage": "mesh_texturing", "engine": "openmvs", "args": [openmvs["texture"], "-i", str(openmvs_workspace / "scene_dense.mvs"), "-m", str(openmvs_workspace / "scene_mesh.ply"), "-o", str(openmvs_workspace / "scene_textured.glb"), "--export-type", "glb", "--max-threads", "4"]},
        ])
    elif colmap_dense_available:
        commands.extend([
        {"stage": "image_undistortion", "args": [executable, "image_undistorter", "--image_path", str(images), "--input_path", str(sparse / "selected"), "--output_path", str(dense), "--output_type", "COLMAP"]},
        {"stage": "dense_stereo", "args": [executable, "patch_match_stereo", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--PatchMatchStereo.geom_consistency", "true"]},
        {"stage": "point_fusion", "args": [executable, "stereo_fusion", "--workspace_path", str(dense), "--workspace_format", "COLMAP", "--input_type", "geometric", "--output_path", str(dense / "fused.ply")]},
        {"stage": "mesh_generation", "args": [executable, "poisson_mesher", "--input_path", str(dense / "fused.ply"), "--output_path", str(dense / "mesh.ply")]},
        ])
    return commands


def _ply_stats(path: Path) -> dict[str, int]:
    """Read vertex and face counts from a PLY header without loading its geometry."""
    stats: dict[str, int] = {}
    if not path.is_file():
        return stats
    with path.open("rb") as stream:
        for raw_line in stream:
            line = raw_line.decode("ascii", errors="replace").strip()
            if line.startswith("element vertex "):
                stats["vertices"] = int(line.rsplit(" ", 1)[1])
            elif line.startswith("element face "):
                stats["faces"] = int(line.rsplit(" ", 1)[1])
            elif line == "end_header":
                break
    return stats


_PLY_SCALARS = {
    "char": "b", "int8": "b", "uchar": "B", "uint8": "B",
    "short": "h", "int16": "h", "ushort": "H", "uint16": "H",
    "int": "i", "int32": "i", "uint": "I", "uint32": "I",
    "float": "f", "float32": "f", "double": "d", "float64": "d",
}


def _read_ply_vertices(path: Path) -> list[tuple[float, float, float]]:
    """Read only XYZ vertex properties from ASCII or little-endian PLY files."""
    with path.open("rb") as stream:
        header: list[str] = []
        while True:
            raw = stream.readline()
            if not raw:
                raise ValueError("PLY header ended unexpectedly")
            line = raw.decode("ascii", errors="strict").strip()
            header.append(line)
            if line == "end_header":
                break
        format_line = next((line for line in header if line.startswith("format ")), "")
        vertex_count = 0
        properties: list[tuple[str, str]] = []
        in_vertices = False
        for line in header:
            if line.startswith("element "):
                parts = line.split()
                in_vertices = parts[1] == "vertex"
                if in_vertices:
                    vertex_count = int(parts[2])
            elif in_vertices and line.startswith("property "):
                parts = line.split()
                if parts[1] == "list":
                    raise ValueError("List properties are not supported on PLY vertices")
                properties.append((parts[1], parts[2]))
        names = [name for _, name in properties]
        if not vertex_count or not {"x", "y", "z"}.issubset(names):
            raise ValueError("PLY has no readable XYZ vertices")
        xyz = [names.index(axis) for axis in "xyz"]
        if "ascii" in format_line:
            vertices = []
            for _ in range(vertex_count):
                values = stream.readline().decode("ascii").split()
                vertices.append(tuple(float(values[index]) for index in xyz))
            return vertices
        if "binary_little_endian" not in format_line:
            raise ValueError("Only ASCII and little-endian binary PLY files are supported")
        try:
            record = struct.Struct("<" + "".join(_PLY_SCALARS[data_type] for data_type, _ in properties))
        except KeyError as error:
            raise ValueError(f"Unsupported PLY scalar type: {error.args[0]}") from error
        vertices = []
        for _ in range(vertex_count):
            raw = stream.read(record.size)
            if len(raw) != record.size:
                raise ValueError("PLY vertex data ended unexpectedly")
            values = record.unpack(raw)
            vertices.append(tuple(float(values[index]) for index in xyz))
        return vertices


def analyze_navigation_mesh(path: Path) -> dict[str, Any]:
    """Turn a reconstructed mesh into conservative, game-ready room metadata."""
    import math

    vertices = _read_ply_vertices(path)
    def percentile(values: list[float], ratio: float) -> float:
        return values[min(len(values) - 1, round((len(values) - 1) * ratio))]

    def subtract(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
        return (a[0] - b[0], a[1] - b[1], a[2] - b[2])

    def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    def cross(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
        return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])

    def normalize(vector: tuple[float, float, float]) -> tuple[float, float, float]:
        length = math.sqrt(dot(vector, vector))
        return tuple(value / max(length, 1e-9) for value in vector)

    # COLMAP coordinates have no gravity guarantee. Fit the dominant room plane
    # and use it as the game floor instead of assuming any source axis is up.
    import random
    randomizer = random.Random(2026)
    sample = vertices[::max(1, len(vertices) // 5000)]
    ranges = [max(point[index] for point in sample) - min(point[index] for point in sample) for index in range(3)]
    tolerance = max(math.sqrt(sum(value * value for value in ranges)) * .006, .015)
    best_normal = (0.0, 1.0, 0.0)
    best_offset = min(point[1] for point in sample)
    best_inliers: list[tuple[float, float, float]] = []
    for _ in range(700):
        first, second, third = randomizer.sample(sample, 3)
        candidate = cross(subtract(second, first), subtract(third, first))
        if dot(candidate, candidate) < 1e-8:
            continue
        normal = normalize(candidate)
        offset = dot(normal, first)
        inliers = [point for point in sample if abs(dot(normal, point) - offset) <= tolerance]
        if len(inliers) > len(best_inliers):
            best_normal, best_offset, best_inliers = normal, offset, inliers
    if len(best_inliers) < max(20, len(sample) // 100):
        best_inliers = [point for point in sample if abs(point[1] - best_offset) <= tolerance]
    origin = tuple(sum(point[index] for point in best_inliers) / len(best_inliers) for index in range(3))
    signed = sorted(dot(best_normal, subtract(point, origin)) for point in sample)
    if percentile(signed, .75) < abs(percentile(signed, .25)):
        best_normal = tuple(-value for value in best_normal)
    reference = (1.0, 0.0, 0.0) if abs(best_normal[0]) < .9 else (0.0, 0.0, 1.0)
    basis_z = normalize(cross(best_normal, reference))
    basis_x = normalize(cross(basis_z, best_normal))
    projected = [(dot(subtract(point, origin), basis_x), dot(subtract(point, origin), best_normal), dot(subtract(point, origin), basis_z)) for point in vertices]
    raw_x = sorted(point[0] for point in projected)
    raw_z = sorted(point[2] for point in projected)
    raw_center_x = (percentile(raw_x, .02) + percentile(raw_x, .98)) / 2
    raw_center_z = (percentile(raw_z, .02) + percentile(raw_z, .98)) / 2
    raw_width = max(percentile(raw_x, .98) - percentile(raw_x, .02), 1e-5)
    raw_depth = max(percentile(raw_z, .98) - percentile(raw_z, .02), 1e-5)
    # Photogrammetry has arbitrary world scale. Expand the longest room axis to
    # a kart-friendly footprint instead of rendering a technically accurate but
    # cramped 12-unit diorama.
    scale = 30 / max(raw_width, raw_depth)
    transformed = [((x - raw_center_x) * scale, height * scale, (z - raw_center_z) * scale) for x, height, z in projected]
    horizontal = [(x, z) for x, height, z in transformed if -.12 <= height <= 2.4]
    if len(horizontal) < 10:
        horizontal = [(x, z) for x, _, z in transformed]
    min_x = percentile(sorted(x for x, _ in horizontal), .01)
    max_x = percentile(sorted(x for x, _ in horizontal), .99)
    min_z = percentile(sorted(z for _, z in horizontal), .01)
    max_z = percentile(sorted(z for _, z in horizontal), .99)
    half_x = max(2.5, (max_x - min_x) / 2)
    half_z = max(2.5, (max_z - min_z) / 2)
    center_x = (min_x + max_x) / 2
    center_z = (min_z + max_z) / 2
    cell_size = .6
    grid_min_x, grid_min_z = center_x - half_x, center_z - half_z
    width = max(3, int((half_x * 2) / cell_size) + 1)
    depth = max(3, int((half_z * 2) / cell_size) + 1)
    counts: dict[tuple[int, int], int] = {}
    for x, height, z in transformed:
        if .18 <= height <= 2.2 and grid_min_x <= x <= grid_min_x + width * cell_size and grid_min_z <= z <= grid_min_z + depth * cell_size:
            cell = (int((x - grid_min_x) / cell_size), int((z - grid_min_z) / cell_size))
            counts[cell] = counts.get(cell, 0) + 1
    occupied = {cell for cell, count in counts.items() if count >= 3}

    def cell_point(cell: tuple[int, int]) -> tuple[float, float]:
        return (grid_min_x + (cell[0] + .5) * cell_size, grid_min_z + (cell[1] + .5) * cell_size)

    def clear(cell: tuple[int, int], radius: int = 1) -> bool:
        x, z = cell
        point_x, point_z = cell_point(cell)
        inside = (
            center_x - half_x + .75 <= point_x <= center_x + half_x - .75
            and center_z - half_z + .75 <= point_z <= center_z + half_z - .75
        )
        return inside and 1 <= x < width - 1 and 1 <= z < depth - 1 and all(
            (x + dx, z + dz) not in occupied
            for dx in range(-radius, radius + 1)
            for dz in range(-radius, radius + 1)
        )

    free = [(x, z) for x in range(width) for z in range(depth) if clear((x, z))]
    if not free:
        free = [(width // 2, depth // 2)]
    spawn_cell = min(free, key=lambda cell: sum(value * value for value in cell_point(cell)))
    chosen: list[tuple[int, int]] = []
    candidates = [cell for cell in free if cell != spawn_cell]
    for _ in range(min(4, len(candidates))):
        anchors = [spawn_cell, *chosen]
        selected = max(candidates, key=lambda cell: min(
            (cell_point(cell)[0] - cell_point(anchor)[0]) ** 2 + (cell_point(cell)[1] - cell_point(anchor)[1]) ** 2
            for anchor in anchors
        ))
        chosen.append(selected)
        candidates.remove(selected)
    if len(chosen) < 2:
        chosen = [spawn_cell]
    checkpoint_center = (
        sum(cell_point(cell)[0] for cell in chosen) / len(chosen),
        sum(cell_point(cell)[1] for cell in chosen) / len(chosen),
    )
    chosen.sort(key=lambda cell: math.atan2(cell_point(cell)[1] - checkpoint_center[1], cell_point(cell)[0] - checkpoint_center[0]))
    obstacle_boxes = [[round(x, 3), round(z, 3), cell_size / 2, cell_size / 2] for x, z in map(cell_point, sorted(occupied))]
    checkpoints = [[round(x, 3), round(z, 3)] for x, z in map(cell_point, chosen)]
    spawn = [round(value, 3) for value in cell_point(spawn_cell)]
    return {
        "schema_version": "1.0",
        "coordinate_system": {
            "origin": list(origin), "basis_x": list(basis_x), "basis_y": list(best_normal), "basis_z": list(basis_z),
            "center_x": raw_center_x, "center_z": raw_center_z, "scale": scale,
            "floor_inliers": len(best_inliers), "floor_tolerance": tolerance,
        },
        "bounds": {"half_x": round(half_x, 3), "half_z": round(half_z, 3), "center_x": round(center_x, 3), "center_z": round(center_z, 3)},
        "spawn": spawn,
        "checkpoints": checkpoints,
        "boost_pads": checkpoints[::2] or [spawn],
        "obstacles": {"boxes": obstacle_boxes, "columns": []},
        "grid": {"cell_size": cell_size, "width": width, "depth": depth, "occupied_cells": len(occupied)},
    }


def _sparse_stats(executable: str, model: Path) -> dict[str, int | float]:
    analysis = subprocess.run(
        [executable, "model_analyzer", "--path", str(model)],
        capture_output=True,
        text=True,
        check=False,
        shell=False,
    )
    analysis_text = analysis.stdout + analysis.stderr
    stats: dict[str, int | float] = {}
    for key, pattern in {
        "registered_images": r"Registered images:\s+(\d+)",
        "points": r"Points:\s+(\d+)",
        "observations": r"Observations:\s+(\d+)",
        "mean_reprojection_error_px": r"Mean reprojection error:\s+([0-9.]+)px",
    }.items():
        match = re.search(pattern, analysis_text)
        if match:
            stats[key] = float(match.group(1)) if key.endswith("_px") else int(match.group(1))
    return stats


def _select_best_sparse_model(executable: str, sparse_root: Path) -> tuple[Path, dict[str, int | float]]:
    candidates = [path for path in sparse_root.iterdir() if path.is_dir() and path.name != "selected"]
    ranked = [(path, _sparse_stats(executable, path)) for path in candidates]
    ranked = [item for item in ranked if "registered_images" in item[1]]
    if not ranked:
        raise RuntimeError("COLMAP completed without producing a readable sparse model")
    best, stats = max(
        ranked,
        key=lambda item: (int(item[1].get("registered_images", 0)), int(item[1].get("points", 0))),
    )
    selected = sparse_root / "selected"
    shutil.copytree(best, selected)
    return selected, stats


def prepare_and_run(settings: Settings, capture_id: str) -> None:
    workspace = settings.reconstruction_root / capture_id
    status_file = status_path(settings, capture_id)
    payload = read_status(settings, capture_id) or queue_reconstruction(settings, capture_id)
    try:
        for stale_key in (
            "mesh_path",
            "model_path",
            "navigation_path",
            "navigation_stats",
            "sparse_model_path",
            "sparse_stats",
            "dense_stats",
            "stage_timings_seconds",
        ):
            payload.pop(stale_key, None)
        payload.update(status="preparing", stage="normalizing_images", progress=5, updated_at=_now())
        _atomic_json(status_file, payload)
        manifest = json.loads((settings.capture_root / capture_id / "manifest.json").read_text(encoding="utf-8"))
        prepared: list[dict[str, Any]] = []
        all_warnings: list[dict[str, Any]] = []
        for index, item in enumerate(manifest["images"], start=1):
            result = _prepare_image(
                _source_path(settings, item["storage_key"]),
                workspace / "images" / f"{index:04d}.jpg",
            )
            prepared.append(result)
            if result["warnings"]:
                all_warnings.append({"file": result["file"], "issues": result["warnings"]})
            payload.update(images=prepared, warnings=all_warnings, progress=5 + round(25 * index / len(manifest["images"])), updated_at=_now())
            _atomic_json(status_file, payload)

        executable = shutil.which(settings.colmap_executable)
        colmap_dense_available = False
        if executable:
            version = subprocess.run(
                [executable, "version"], capture_output=True, text=True, check=False, shell=False
            )
            colmap_dense_available = "without GPU support" not in (version.stdout + version.stderr)
        openmvs = _openmvs_executables(settings.openmvs_directory)
        commands = _commands(
            executable or settings.colmap_executable,
            workspace,
            colmap_dense_available,
            openmvs,
        )
        backend = "openmvs_cpu" if openmvs else ("colmap_gpu" if colmap_dense_available else "sparse_only")
        pipeline = {"schema_version": "1.1", "capture_id": capture_id, "backend": backend, "commands": commands}
        _atomic_json(workspace / "pipeline.json", pipeline)
        payload.update(commands=commands, backend=backend, updated_at=_now())
        _atomic_json(status_file, payload)
        if not executable:
            payload.update(status="awaiting_colmap", stage="dependency_check", progress=30, commands=commands, error="COLMAP executable was not found", updated_at=_now())
            _atomic_json(status_file, payload)
            return

        # These are generated artifacts. Always start an attempt from a clean
        # COLMAP database so a crashed or cancelled process cannot poison a retry.
        database_path = workspace / "database.db"
        if database_path.exists():
            database_path.unlink()
        for generated_directory in (workspace / "sparse", workspace / "dense", workspace / "openmvs"):
            if generated_directory.exists():
                shutil.rmtree(generated_directory)
        (workspace / "sparse").mkdir(exist_ok=True)
        if openmvs:
            (workspace / "openmvs").mkdir(exist_ok=True)
        log_path = workspace / "colmap.log"
        timings: dict[str, float] = {}
        with log_path.open("a", encoding="utf-8") as log:
            for step, command in enumerate(commands, start=1):
                payload.update(status="running", stage=command["stage"], progress=30 + round(65 * (step - 1) / len(commands)), error=None, stage_timings_seconds=timings, updated_at=_now())
                _atomic_json(status_file, payload)
                started = time.monotonic()
                completed = subprocess.run(
                    command["args"],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    check=False,
                    shell=False,
                    cwd=workspace / "openmvs" if command.get("engine") == "openmvs" else workspace,
                )
                timings[command["stage"]] = round(time.monotonic() - started, 2)
                if completed.returncode:
                    raise RuntimeError(f"{command.get('engine', 'colmap').upper()} stage {command['stage']} exited with code {completed.returncode}")
                if command["stage"] == "sparse_mapping":
                    _, sparse_stats = _select_best_sparse_model(executable, workspace / "sparse")
                    payload.update(sparse_stats=sparse_stats, sparse_model_path="sparse/selected", updated_at=_now())
                    _atomic_json(status_file, payload)
                    registered = int(sparse_stats.get("registered_images", 0))
                    sparse_points = int(sparse_stats.get("points", 0))
                    minimum_registered = max(12, math.ceil(len(prepared) * .8))
                    if registered < minimum_registered or sparse_points < registered * 150:
                        raise ReconstructionQualityError(
                            f"Only {registered} of {len(prepared)} photos aligned into one room map "
                            f"({sparse_points} stable points). Recapture with 30–50 photos, take one small "
                            "step between photos, and keep at least 70% of the previous view visible."
                        )
        sparse_model = workspace / "sparse" / "selected"
        if not sparse_model.is_dir():
            raise RuntimeError("COLMAP completed without selecting a sparse model")
        sparse_stats = _sparse_stats(executable, sparse_model)
        if openmvs:
            mesh_path = workspace / "openmvs" / "scene_mesh.ply"
            dense_stats = _ply_stats(workspace / "openmvs" / "scene_dense.ply")
            dense_stats["points"] = dense_stats.pop("vertices", 0)
            dense_stats.update(_ply_stats(workspace / "openmvs" / "scene_mesh.ply"))
            if dense_stats.get("points", 0) < 50000 or dense_stats.get("faces", 0) < 20000:
                raise ReconstructionQualityError(
                    f"The capture aligned, but produced only {dense_stats.get('points', 0)} dense points and "
                    f"{dense_stats.get('faces', 0)} faces—too little room surface for safe gameplay. "
                    "Recapture with closer, overlapping viewpoints around the entire perimeter."
                )
            navigation = analyze_navigation_mesh(mesh_path)
            _atomic_json(workspace / "openmvs" / "navigation.json", navigation)
            payload.update(status="complete", stage="room_ready", progress=100, mesh_path="openmvs/scene_mesh.ply", model_path="openmvs/scene_textured.glb", navigation_path="openmvs/navigation.json", navigation_stats=navigation["grid"], sparse_stats=sparse_stats, dense_stats=dense_stats, stage_timings_seconds=timings, error=None, updated_at=_now())
        elif colmap_dense_available:
            payload.update(status="complete", stage="mesh_ready", progress=100, mesh_path="dense/mesh.ply", sparse_stats=sparse_stats, stage_timings_seconds=timings, error=None, updated_at=_now())
        else:
            payload.update(status="sparse_complete", stage="dense_backend_unavailable", progress=65, sparse_model_path="sparse/selected", sparse_stats=sparse_stats, stage_timings_seconds=timings, error="No OpenMVS CPU tools or COLMAP GPU dense backend were found", updated_at=_now())
        _atomic_json(status_file, payload)
    except ReconstructionQualityError as error:
        payload.update(status="quality_failed", stage="recapture_required", progress=100, error=str(error), updated_at=_now())
        _atomic_json(status_file, payload)
    except Exception as error:
        payload.update(status="failed", stage="failed", error=str(error), updated_at=_now())
        _atomic_json(status_file, payload)
