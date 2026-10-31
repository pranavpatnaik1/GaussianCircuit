from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


CaptureStatus = Literal[
    "waiting_for_phone",
    "capturing",
    "ready_for_reconstruction",
    "expired",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CreateCaptureResponse(ApiModel):
    id: str
    code: str
    desktop_token: str
    capture_url: str
    expires_at: datetime


class JoinCaptureRequest(ApiModel):
    code: str = Field(min_length=6, max_length=12)


class JoinCaptureResponse(ApiModel):
    id: str
    phone_token: str
    expires_at: datetime
    minimum_images: int
    maximum_images: int


class PhotoSummary(ApiModel):
    id: str
    original_name: str
    media_type: str
    size_bytes: int
    sha256: str


class CaptureSnapshot(ApiModel):
    id: str
    code: str
    status: CaptureStatus
    created_at: datetime
    updated_at: datetime
    expires_at: datetime
    completed_at: datetime | None
    photo_count: int
    total_bytes: int
    revision: int
    minimum_images: int
    photos: list[PhotoSummary] = Field(default_factory=list)


class UploadResponse(ApiModel):
    accepted: list[PhotoSummary]
    duplicate_count: int
    snapshot: CaptureSnapshot


class ReconstructionStatus(ApiModel):
    schema_version: str
    capture_id: str
    backend: str | None = None
    status: str
    stage: str
    progress: int
    created_at: datetime
    updated_at: datetime
    images: list[dict] = Field(default_factory=list)
    warnings: list[dict] = Field(default_factory=list)
    commands: list[dict] = Field(default_factory=list)
    error: str | None = None
    mesh_path: str | None = None
    model_path: str | None = None
    navigation_path: str | None = None
    navigation_stats: dict = Field(default_factory=dict)
    sparse_model_path: str | None = None
    sparse_stats: dict = Field(default_factory=dict)
    dense_stats: dict = Field(default_factory=dict)
    stage_timings_seconds: dict[str, float] = Field(default_factory=dict)
