"""Dependency-free SVG rendering for planner inspection."""

from html import escape

from .models import Scene


def render_svg(scene: Scene, result: dict) -> str:
    left, bottom, right, top = scene.floor.polygon.bounds
    width, height = right - left, top - bottom
    padding = max(width, height) * 0.06
    parts = [
        (
            f'<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="{left-padding} {bottom-padding} {width+2*padding} {height+2*padding}" '
            'width="960" role="img">'
        ),
        f'<title>{escape(scene.id)}: {escape(result["status"])}</title>',
        '<rect x="-10000" y="-10000" width="20000" height="20000" fill="#101827"/>',
    ]
    groups = (
        ([scene.floor], "#dce7e5"),
        (scene.obstacles, "#475569"),
        (scene.unknown_regions, "#edb94b"),
    )
    for regions, fill in groups:
        for region in regions:
            points = " ".join(f"{x},{y}" for x, y in region.vertices)
            parts.append(
                f'<polygon points="{points}" fill="{fill}"><title>{escape(region.id)}</title></polygon>'
            )
    if result["track"]:
        points = result["track"]["centerline"]
        coordinates = " ".join(f"{x},{y}" for x, y in [*points, points[0]])
        width = result["config"]["track_width"]
        parts.append(
            f'<polyline points="{coordinates}" fill="none" stroke="#15936d" '
            f'stroke-width="{width}" stroke-linejoin="round"/>'
        )
        parts.append(
            f'<polyline points="{coordinates}" fill="none" stroke="#fff" '
            'stroke-width="0.025" stroke-dasharray="0.12 0.10"/>'
        )
    parts.append("</svg>")
    return "\n".join(parts)
