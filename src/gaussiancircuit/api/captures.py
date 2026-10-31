import asyncio
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image, ImageChops, ImageFilter, ImageOps, ImageStat

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .config import Settings
from .schemas import (
    CaptureSnapshot,
    CreateCaptureResponse,
    JoinCaptureRequest,
    JoinCaptureResponse,
    PhotoSummary,
    ReconstructionStatus,
    UploadResponse,
)
from ..reconstruction import prepare_and_run, queue_reconstruction, read_status
from .security import bearer_token, new_pairing_code, new_token, normalize_code, token_hash, token_matches
from .tables import CapturePhoto, CaptureSession, utcnow

ALLOWED_MEDIA_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/heic": ".heic",
    "image/heif": ".heif",
}
ALLOWED_VIDEO_TYPES = {
    "video/mp4": ".mp4",
    "video/quicktime": ".mov",
    "video/x-m4v": ".m4v",
    "video/webm": ".webm",
}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _expired(capture: CaptureSession) -> bool:
    return _aware(capture.expires_at) <= datetime.now(timezone.utc)


def _snapshot(capture: CaptureSession, settings: Settings) -> CaptureSnapshot:
    return CaptureSnapshot(
        id=capture.id,
        code=capture.code,
        status=capture.status,
        created_at=capture.created_at,
        updated_at=capture.updated_at,
        expires_at=capture.expires_at,
        completed_at=capture.completed_at,
        photo_count=len(capture.photos),
        total_bytes=capture.total_bytes,
        revision=capture.revision,
        minimum_images=settings.min_capture_images,
        photos=[
            PhotoSummary(
                id=photo.id,
                original_name=photo.original_name,
                media_type=photo.media_type,
                size_bytes=photo.size_bytes,
                sha256=photo.sha256,
            )
            for photo in sorted(capture.photos, key=lambda item: _aware(item.created_at))
        ],
    )


def _load_capture(db: Session, capture_id: str) -> CaptureSession:
    capture = db.scalar(
        select(CaptureSession)
        .where(CaptureSession.id == capture_id)
        .options(selectinload(CaptureSession.photos))
    )
    if capture is None:
        raise HTTPException(status_code=404, detail="Capture session not found")
    if _expired(capture) and capture.status not in {"ready_for_reconstruction", "expired"}:
        capture.status = "expired"
        capture.updated_at = utcnow()
        capture.revision += 1
        db.commit()
    return capture


def _authorize_desktop(capture: CaptureSession, token: str) -> None:
    if not token_matches(token, capture.desktop_token_hash):
        raise HTTPException(status_code=403, detail="Invalid desktop credential")


def _authorize_phone(capture: CaptureSession, token: str) -> None:
    if not token_matches(token, capture.phone_token_hash):
        raise HTTPException(status_code=403, detail="Invalid phone credential")


def _looks_like_image(data: bytes, media_type: str) -> bool:
    if media_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if media_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if media_type in {"image/heic", "image/heif"}:
        return len(data) >= 12 and data[4:8] == b"ftyp"
    return False


def _select_video_frames(paths: list[Path], maximum: int) -> list[Path]:
    """Keep sharp, exposed, visually distinct frames from a guided scan."""
    selected: list[Path] = []
    previous: Image.Image | None = None
    for path in paths:
        with Image.open(path) as opened:
            grayscale = ImageOps.grayscale(opened)
            sample = grayscale.copy()
            sample.thumbnail((512, 512))
            brightness = ImageStat.Stat(sample).mean[0]
            sharpness = ImageStat.Stat(sample.filter(ImageFilter.FIND_EDGES)).var[0]
            thumbnail = grayscale.resize((64, 64))
            difference = 100 if previous is None else ImageStat.Stat(ImageChops.difference(thumbnail, previous)).mean[0]
        if 20 <= brightness <= 240 and sharpness >= 20 and difference >= 3.5:
            selected.append(path)
            previous = thumbnail
        if len(selected) >= maximum:
            break
    return selected


def build_router(settings: Settings, db_dependency) -> APIRouter:
    router = APIRouter(prefix="/capture-sessions", tags=["capture"])

    @router.post("", response_model=CreateCaptureResponse, status_code=status.HTTP_201_CREATED)
    def create_capture(db: Session = Depends(db_dependency)):
        desktop_token = new_token()
        expires_at = utcnow() + timedelta(minutes=settings.session_minutes)
        for _ in range(20):
            capture = CaptureSession(
                code=new_pairing_code(),
                desktop_token_hash=token_hash(desktop_token),
                expires_at=expires_at,
            )
            db.add(capture)
            try:
                db.commit()
                db.refresh(capture)
                break
            except IntegrityError:
                db.rollback()
        else:
            raise HTTPException(status_code=503, detail="Could not allocate a capture code")
        return CreateCaptureResponse(
            id=capture.id,
            code=capture.code,
            desktop_token=desktop_token,
            capture_url=f"{settings.public_web_url}/capture?code={capture.code}",
            expires_at=capture.expires_at,
        )

    @router.post("/join", response_model=JoinCaptureResponse)
    def join_capture(payload: JoinCaptureRequest, db: Session = Depends(db_dependency)):
        code = normalize_code(payload.code)
        capture = db.scalar(select(CaptureSession).where(CaptureSession.code == code))
        if capture is None:
            raise HTTPException(status_code=404, detail="Capture code not found")
        if _expired(capture) or capture.status == "expired":
            raise HTTPException(status_code=410, detail="Capture code has expired")
        if capture.status == "ready_for_reconstruction":
            raise HTTPException(status_code=409, detail="Capture is already complete")
        phone_token = new_token()
        capture.phone_token_hash = token_hash(phone_token)
        capture.status = "capturing"
        capture.updated_at = utcnow()
        capture.revision += 1
        db.commit()
        return JoinCaptureResponse(
            id=capture.id,
            phone_token=phone_token,
            expires_at=capture.expires_at,
            minimum_images=settings.min_capture_images,
            maximum_images=settings.max_capture_images,
        )

    @router.get("/{capture_id}", response_model=CaptureSnapshot)
    def get_capture(
        capture_id: str,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        return _snapshot(capture, settings)

    @router.post("/{capture_id}/photos", response_model=UploadResponse)
    async def upload_photos(
        capture_id: str,
        files: list[UploadFile] = File(...),
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_phone(capture, token)
        if capture.status != "capturing":
            raise HTTPException(status_code=409, detail="Capture is not accepting photos")
        if len(capture.photos) + len(files) > settings.max_capture_images:
            raise HTTPException(status_code=413, detail="Capture contains too many images")

        accepted: list[CapturePhoto] = []
        duplicates = 0
        capture_directory = settings.capture_root / capture.id
        capture_directory.mkdir(parents=True, exist_ok=True)

        for upload in files:
            media_type = (upload.content_type or "").lower()
            if media_type not in ALLOWED_MEDIA_TYPES:
                raise HTTPException(status_code=415, detail=f"Unsupported image type: {media_type or 'unknown'}")
            data = await upload.read(settings.max_image_bytes + 1)
            if not data or len(data) > settings.max_image_bytes:
                raise HTTPException(status_code=413, detail="An image is empty or exceeds the size limit")
            if not _looks_like_image(data, media_type):
                raise HTTPException(status_code=422, detail="Image content does not match its declared type")
            digest = hashlib.sha256(data).hexdigest()
            if any(photo.sha256 == digest for photo in capture.photos) or any(
                photo.sha256 == digest for photo in accepted
            ):
                duplicates += 1
                continue
            if capture.total_bytes + sum(photo.size_bytes for photo in accepted) + len(data) > settings.max_session_bytes:
                raise HTTPException(status_code=413, detail="Capture session exceeds its storage limit")
            photo_id = str(uuid4())
            extension = ALLOWED_MEDIA_TYPES[media_type]
            final_path = capture_directory / f"{photo_id}{extension}"
            temporary_path = capture_directory / f".{photo_id}.upload"
            temporary_path.write_bytes(data)
            temporary_path.replace(final_path)
            accepted.append(
                CapturePhoto(
                    id=photo_id,
                    capture_session_id=capture.id,
                    original_name=Path(upload.filename or f"capture{extension}").name[:255],
                    storage_key=str(final_path.relative_to(settings.capture_root)),
                    media_type=media_type,
                    size_bytes=len(data),
                    sha256=digest,
                )
            )

        capture.photos.extend(accepted)
        capture.total_bytes += sum(photo.size_bytes for photo in accepted)
        capture.updated_at = utcnow()
        capture.revision += 1
        db.commit()
        capture = _load_capture(db, capture_id)
        return UploadResponse(
            accepted=[
                PhotoSummary(
                    id=photo.id,
                    original_name=photo.original_name,
                    media_type=photo.media_type,
                    size_bytes=photo.size_bytes,
                    sha256=photo.sha256,
                )
                for photo in accepted
            ],
            duplicate_count=duplicates,
            snapshot=_snapshot(capture, settings),
        )

    @router.post("/{capture_id}/video", response_model=UploadResponse)
    async def upload_scan_video(
        capture_id: str,
        video: UploadFile = File(...),
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_phone(capture, token)
        if capture.status != "capturing":
            raise HTTPException(status_code=409, detail="Capture is not accepting a room scan")
        media_type = (video.content_type or "").lower().split(";", 1)[0]
        if media_type not in ALLOWED_VIDEO_TYPES:
            raise HTTPException(status_code=415, detail=f"Unsupported video type: {media_type or 'unknown'}")
        executable = shutil.which(settings.ffmpeg_executable) or settings.ffmpeg_executable
        if not Path(executable).is_file() and shutil.which(executable) is None:
            raise HTTPException(status_code=503, detail="FFmpeg is required to process guided room scans")
        capture_directory = settings.capture_root / capture.id
        capture_directory.mkdir(parents=True, exist_ok=True)
        scan_id = str(uuid4())
        source = capture_directory / f".{scan_id}{ALLOWED_VIDEO_TYPES[media_type]}"
        frames = capture_directory / f".{scan_id}-frames"
        frames.mkdir()
        size = 0
        try:
            with source.open("wb") as stream:
                while chunk := await video.read(1024 * 1024):
                    size += len(chunk)
                    if size > settings.max_video_bytes:
                        raise HTTPException(status_code=413, detail="Room scan video exceeds the 500 MB limit")
                    stream.write(chunk)
            completed = subprocess.run(
                [
                    executable, "-hide_banner", "-loglevel", "error", "-i", str(source),
                    "-vf", "fps=1.2,scale=1920:-2:force_original_aspect_ratio=decrease",
                    "-an", "-q:v", "2", "-frames:v", "120", str(frames / "%04d.jpg"),
                ],
                capture_output=True,
                text=True,
                check=False,
                shell=False,
            )
            if completed.returncode:
                raise HTTPException(status_code=422, detail="The room scan video could not be decoded")
            chosen = _select_video_frames(sorted(frames.glob("*.jpg")), settings.max_capture_images - len(capture.photos))
            if len(chosen) < settings.min_capture_images:
                raise HTTPException(
                    status_code=422,
                    detail=f"Only {len(chosen)} usable viewpoints were found. Record for 45–90 seconds, walk slowly, and avoid blur.",
                )
            if capture.total_bytes + sum(frame.stat().st_size for frame in chosen) > settings.max_session_bytes:
                raise HTTPException(status_code=413, detail="Extracted scan frames exceed the capture storage limit")
            accepted: list[CapturePhoto] = []
            for index, frame in enumerate(chosen, start=1):
                data = frame.read_bytes()
                digest = hashlib.sha256(data).hexdigest()
                if any(photo.sha256 == digest for photo in capture.photos) or any(photo.sha256 == digest for photo in accepted):
                    continue
                photo_id = str(uuid4())
                final_path = capture_directory / f"{photo_id}.jpg"
                frame.replace(final_path)
                accepted.append(CapturePhoto(
                    id=photo_id,
                    capture_session_id=capture.id,
                    original_name=f"guided-scan-{index:03d}.jpg",
                    storage_key=str(final_path.relative_to(settings.capture_root)),
                    media_type="image/jpeg",
                    size_bytes=len(data),
                    sha256=digest,
                ))
            accepted_bytes = sum(photo.size_bytes for photo in accepted)
            capture.photos.extend(accepted)
            capture.total_bytes += accepted_bytes
            capture.updated_at = utcnow()
            capture.revision += 1
            db.commit()
            capture = _load_capture(db, capture_id)
            return UploadResponse(
                accepted=[PhotoSummary(id=photo.id, original_name=photo.original_name, media_type=photo.media_type, size_bytes=photo.size_bytes, sha256=photo.sha256) for photo in accepted],
                duplicate_count=0,
                snapshot=_snapshot(capture, settings),
            )
        finally:
            source.unlink(missing_ok=True)
            shutil.rmtree(frames, ignore_errors=True)

    @router.post("/{capture_id}/finish", response_model=CaptureSnapshot)
    def finish_capture(
        capture_id: str,
        background_tasks: BackgroundTasks,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_phone(capture, token)
        if capture.status != "capturing":
            raise HTTPException(status_code=409, detail="Capture cannot be completed in its current state")
        if len(capture.photos) < settings.min_capture_images:
            raise HTTPException(
                status_code=422,
                detail=f"At least {settings.min_capture_images} distinct images are required",
            )
        capture.status = "ready_for_reconstruction"
        capture.completed_at = utcnow()
        capture.updated_at = capture.completed_at
        capture.revision += 1
        db.commit()
        capture = _load_capture(db, capture_id)

        manifest = {
            "schema_version": "1.0",
            "capture_id": capture.id,
            "capture_mode": "web_photo_set",
            "created_at": capture.created_at.isoformat(),
            "completed_at": capture.completed_at.isoformat(),
            "images": [
                {
                    "id": photo.id,
                    "storage_key": photo.storage_key,
                    "media_type": photo.media_type,
                    "size_bytes": photo.size_bytes,
                    "sha256": photo.sha256,
                }
                for photo in capture.photos
            ],
        }
        manifest_path = settings.capture_root / capture.id / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        queue_reconstruction(settings, capture.id)
        background_tasks.add_task(prepare_and_run, settings, capture.id)
        return _snapshot(capture, settings)

    @router.get("/{capture_id}/reconstruction", response_model=ReconstructionStatus)
    def reconstruction_status(
        capture_id: str,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        reconstruction = read_status(settings, capture_id)
        if reconstruction is None:
            raise HTTPException(status_code=404, detail="Reconstruction has not been queued")
        return reconstruction

    @router.get("/{capture_id}/reconstruction/mesh")
    def reconstruction_mesh(
        capture_id: str,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        reconstruction = read_status(settings, capture_id)
        if reconstruction is None or reconstruction.get("status") != "complete":
            raise HTTPException(status_code=409, detail="Reconstruction mesh is not ready")
        mesh_path = reconstruction.get("mesh_path")
        if not mesh_path:
            raise HTTPException(status_code=404, detail="Reconstruction has no mesh")
        workspace = (settings.reconstruction_root / capture_id).resolve()
        mesh = (workspace / mesh_path).resolve()
        if not mesh.is_relative_to(workspace) or mesh.suffix.lower() != ".ply":
            raise HTTPException(status_code=500, detail="Reconstruction mesh path is invalid")
        if not mesh.is_file():
            raise HTTPException(status_code=404, detail="Reconstruction mesh file is missing")
        return FileResponse(
            mesh,
            media_type="application/octet-stream",
            filename=f"gaussiancircuit-{capture_id}.ply",
        )

    @router.get("/{capture_id}/reconstruction/navigation")
    def reconstruction_navigation(
        capture_id: str,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        reconstruction = read_status(settings, capture_id)
        navigation_path = reconstruction.get("navigation_path") if reconstruction else None
        if not navigation_path:
            raise HTTPException(status_code=404, detail="Room navigation map is not ready")
        workspace = (settings.reconstruction_root / capture_id).resolve()
        navigation = (workspace / navigation_path).resolve()
        if not navigation.is_relative_to(workspace) or navigation.suffix.lower() != ".json":
            raise HTTPException(status_code=500, detail="Room navigation path is invalid")
        if not navigation.is_file():
            raise HTTPException(status_code=404, detail="Room navigation map is missing")
        return json.loads(navigation.read_text(encoding="utf-8"))

    @router.get("/{capture_id}/reconstruction/model")
    def reconstruction_model(
        capture_id: str,
        token: str = Depends(bearer_token),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        reconstruction = read_status(settings, capture_id)
        model_path = reconstruction.get("model_path") if reconstruction else None
        if not model_path or reconstruction.get("status") != "complete":
            raise HTTPException(status_code=409, detail="A playable textured room is not ready")
        workspace = (settings.reconstruction_root / capture_id).resolve()
        model = (workspace / model_path).resolve()
        if not model.is_relative_to(workspace) or model.suffix.lower() != ".glb":
            raise HTTPException(status_code=500, detail="Reconstruction model path is invalid")
        if not model.is_file():
            raise HTTPException(status_code=404, detail="Reconstruction model is missing")
        return FileResponse(model, media_type="model/gltf-binary", filename=f"gaussiancircuit-{capture_id}.glb")

    @router.get("/{capture_id}/reconstruction/assets/{filename}")
    def reconstruction_asset(
        capture_id: str,
        filename: str,
        token: str = Query(),
        db: Session = Depends(db_dependency),
    ):
        capture = _load_capture(db, capture_id)
        _authorize_desktop(capture, token)
        if Path(filename).name != filename or Path(filename).suffix.lower() not in {".png", ".jpg", ".jpeg"}:
            raise HTTPException(status_code=404, detail="Reconstruction asset is invalid")
        workspace = (settings.reconstruction_root / capture_id / "openmvs").resolve()
        asset = (workspace / filename).resolve()
        if not asset.is_relative_to(workspace) or not asset.is_file():
            raise HTTPException(status_code=404, detail="Reconstruction asset is missing")
        media_type = "image/png" if asset.suffix.lower() == ".png" else "image/jpeg"
        return FileResponse(asset, media_type=media_type)

    @router.get("/{capture_id}/events")
    async def capture_events(capture_id: str, token: str, db: Session = Depends(db_dependency)):
        initial = _load_capture(db, capture_id)
        _authorize_desktop(initial, token)

        async def events():
            last_revision = -1
            while True:
                db.expire_all()
                capture = _load_capture(db, capture_id)
                _authorize_desktop(capture, token)
                if capture.revision != last_revision:
                    snapshot = _snapshot(capture, settings)
                    yield f"id: {capture.revision}\nevent: capture\ndata: {snapshot.model_dump_json()}\n\n"
                    last_revision = capture.revision
                if capture.status in {"ready_for_reconstruction", "expired"}:
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(1)

        return StreamingResponse(
            events(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
        )

    return router
