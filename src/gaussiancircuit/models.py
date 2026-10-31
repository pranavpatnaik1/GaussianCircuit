"""Strict, versioned metric contracts shared by planning and future adapters."""

from math import hypot, radians, tan
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from shapely.geometry import Polygon

Point = tuple[float, float]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, frozen=True)


class Region(Contract):
    id: str = Field(min_length=1)
    vertices: list[Point] = Field(min_length=3)

    @model_validator(mode="after")
    def validate_polygon(self):
        if not self.polygon.is_valid or self.polygon.area <= 1e-9:
            raise ValueError("region must be a simple polygon with positive area")
        return self

    @property
    def polygon(self) -> Polygon:
        return Polygon(self.vertices)


class Scene(Contract):
    schema_version: Literal["1.0"] = "1.0"
    id: str = Field(min_length=1)
    revision: int = Field(default=1, ge=1)
    units: Literal["meters"] = "meters"
    coordinate_frame: Literal["floor_xy_z_up"] = "floor_xy_z_up"
    source: Literal["synthetic", "reconstruction"] = "synthetic"
    floor: Region
    obstacles: list[Region] = Field(default_factory=list)
    unknown_regions: list[Region] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_scene(self):
        regions = [self.floor, *self.obstacles, *self.unknown_regions]
        ids = [region.id for region in regions]
        if len(ids) != len(set(ids)):
            raise ValueError("region IDs must be unique")
        for region in [*self.obstacles, *self.unknown_regions]:
            if not self.floor.polygon.covers(region.polygon):
                raise ValueError(f"region {region.id!r} extends outside the floor")
        return self


class Vehicle(Contract):
    width: float = Field(default=0.16, gt=0)
    length: float = Field(default=0.30, gt=0)
    wheelbase: float = Field(default=0.18, gt=0)
    rear_overhang: float = Field(default=0.06, ge=0)
    max_steering_degrees: float = Field(default=35.0, gt=0, lt=80)

    @model_validator(mode="after")
    def validate_axles(self):
        if self.rear_overhang + self.wheelbase > self.length:
            raise ValueError("wheelbase and rear overhang must fit inside vehicle length")
        return self

    @property
    def footprint_radius(self) -> float:
        """Conservative disk enclosing the vehicle about its rear axle."""
        reach = max(self.rear_overhang, self.length - self.rear_overhang)
        return hypot(reach, self.width / 2)

    @property
    def turning_radius(self) -> float:
        return self.wheelbase / tan(radians(self.max_steering_degrees))


class PlannerConfig(Contract):
    track_width: float = Field(default=0.40, gt=0)
    safety_margin: float = Field(default=0.08, ge=0)
    sample_step: float = Field(default=0.05, gt=0, le=0.25)
    candidate_levels: int = Field(default=12, ge=2, le=40)
