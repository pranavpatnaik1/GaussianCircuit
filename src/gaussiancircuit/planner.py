"""Deterministic bounded-search planning baseline."""

from math import ceil, cos, pi, sin

from shapely.geometry import LineString
from shapely.ops import unary_union

from .models import PlannerConfig, Point, Scene, Vehicle

ALGORITHM = "nested-rounded-rectangles-v1"


def rounded_rectangle(bounds, radius: float, step: float) -> list[Point]:
    left, bottom, right, top = bounds
    if right - left <= 2 * radius or top - bottom <= 2 * radius:
        return []
    points: list[Point] = []
    corners = (
        (right - radius, bottom + radius, -pi / 2),
        (right - radius, top - radius, 0),
        (left + radius, top - radius, pi / 2),
        (left + radius, bottom + radius, pi),
    )
    samples = max(4, ceil(pi * radius / (2 * step)))
    for cx, cy, start in corners:
        for index in range(samples + 1):
            angle = start + index * pi / (2 * samples)
            points.append((cx + radius * cos(angle), cy + radius * sin(angle)))
    return points


def validate_path(
    scene: Scene,
    vehicle: Vehicle,
    config: PlannerConfig,
    points: list[Point],
) -> dict:
    if len(points) < 4:
        return {"valid": False, "reason": "too_few_points"}

    line = LineString([*points, points[0]])
    if not line.is_simple or line.length <= 0:
        return {"valid": False, "reason": "self_intersection_or_degenerate_path"}

    # This corridor covers every point on every connecting segment. The disk is
    # conservative for the rectangular vehicle at every possible heading.
    radius = max(config.track_width / 2, vehicle.footprint_radius)
    protected = line.buffer(radius + config.safety_margin, join_style=2)
    if not scene.floor.polygon.covers(protected):
        return {"valid": False, "reason": "floor_clearance"}

    blocked = [region.polygon for region in [*scene.obstacles, *scene.unknown_regions]]
    occupied = unary_union(blocked)
    if blocked and protected.intersects(occupied):
        return {"valid": False, "reason": "obstacle_or_unknown_clearance"}

    clearance = line.distance(scene.floor.polygon.boundary)
    if blocked:
        clearance = min(clearance, line.distance(occupied))
    return {
        "valid": True,
        "reason": "passed",
        "path_length_m": line.length,
        "minimum_centerline_clearance_m": clearance,
        "protected_radius_m": radius + config.safety_margin,
        "validation_level": "continuous_polyline_clearance",
        "dynamics_validated": False,
    }


def plan(scene: Scene, vehicle: Vehicle, config: PlannerConfig) -> dict:
    if config.track_width < vehicle.width:
        raise ValueError("track width must be at least the vehicle width")

    left, bottom, right, top = scene.floor.polygon.bounds
    margin = max(config.track_width / 2, vehicle.footprint_radius) + config.safety_margin
    minimum_inset = margin + config.sample_step
    corner_radius = max(vehicle.turning_radius, config.track_width / 2)
    rejection_counts: dict[str, int] = {}
    best = None
    attempted = 0

    for x_index in range(config.candidate_levels):
        inset_x = minimum_inset + x_index / (config.candidate_levels - 1) * max(
            0, (right - left) * 0.45 - minimum_inset
        )
        for y_index in range(config.candidate_levels):
            inset_y = minimum_inset + y_index / (config.candidate_levels - 1) * max(
                0, (top - bottom) * 0.45 - minimum_inset
            )
            points = rounded_rectangle(
                (left + inset_x, bottom + inset_y, right - inset_x, top - inset_y),
                corner_radius,
                config.sample_step,
            )
            attempted += 1
            validation = validate_path(scene, vehicle, config, points)
            if not validation["valid"]:
                reason = validation["reason"]
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
                continue
            if best is None or validation["path_length_m"] > best["validation"]["path_length_m"]:
                best = {"centerline": points, "validation": validation}

    return {
        "schema_version": "1.0",
        "scene_id": scene.id,
        "scene_revision": scene.revision,
        "algorithm": ALGORITHM,
        "status": "valid" if best else "no_candidate_found",
        "vehicle": vehicle.model_dump(),
        "config": config.model_dump(),
        "candidate_count": attempted,
        "rejection_counts": rejection_counts,
        "unknown_policy": "blocked",
        "track": best,
        "limitations": [
            "The bounded nested-rectangle search can miss valid asymmetric circuits.",
            "The output is a sampled polyline, not a smooth optimized trajectory.",
            "Clearance is conservative and vehicle dynamics are not yet validated.",
        ],
    }
