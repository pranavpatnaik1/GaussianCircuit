import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from .models import PlannerConfig, Scene, Vehicle
from .planner import plan
from .preview import render_svg


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Generate and validate an indoor circuit")
    parser.add_argument("scene", type=Path, help="metric scene JSON")
    parser.add_argument("--output", type=Path, default=Path("outputs/demo"))
    parser.add_argument("--track-width", type=float, default=0.40)
    parser.add_argument("--safety-margin", type=float, default=0.08)
    parser.add_argument("--vehicle", type=Path, help="optional vehicle JSON")
    args = parser.parse_args(argv)

    try:
        scene = Scene.model_validate_json(args.scene.read_text(encoding="utf-8"))
        vehicle = (
            Vehicle.model_validate_json(args.vehicle.read_text(encoding="utf-8"))
            if args.vehicle
            else Vehicle()
        )
        config = PlannerConfig(
            track_width=args.track_width,
            safety_margin=args.safety_margin,
        )
        result = plan(scene, vehicle, config)
        args.output.mkdir(parents=True, exist_ok=True)
        (args.output / "scene.json").write_text(scene.model_dump_json(indent=2), encoding="utf-8")
        (args.output / "report.json").write_text(
            json.dumps(result, indent=2, allow_nan=False), encoding="utf-8"
        )
        (args.output / "preview.svg").write_text(render_svg(scene, result), encoding="utf-8")
    except (OSError, ValidationError, ValueError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2

    print(f'{scene.id}: {result["status"]}; {result["candidate_count"]} candidates')
    print(f'Report: {args.output / "report.json"}')
    print(f'Preview: {args.output / "preview.svg"}')
    return 0 if result["track"] else 3
