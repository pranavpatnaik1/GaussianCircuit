from dataclasses import dataclass
from pathlib import Path
import os
import shutil
import socket


def _local_web_url() -> str:
    """Return a phone-reachable development URL when no public URL is configured."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            # UDP connect chooses the active interface without sending a packet.
            connection.connect(("192.0.2.1", 80))
            address = connection.getsockname()[0]
        if address and not address.startswith("127."):
            return f"http://{address}:5173"
    except OSError:
        pass
    return "http://localhost:5173"


def _project_colmap() -> str:
    install_root = Path(__file__).resolve().parents[3] / "tools" / "colmap"
    candidates = list(install_root.glob("*/COLMAP.bat"))

    def version(path: Path) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in path.parent.name.split("."))
        except ValueError:
            return (0,)

    if candidates:
        return str(max(candidates, key=version))
    return "colmap"


def _project_openmvs() -> str:
    install_root = Path(__file__).resolve().parents[3] / "tools" / "openmvs"
    candidates = list(install_root.glob("*/vc*/x64/Release"))

    def version(path: Path) -> tuple[int, ...]:
        try:
            return tuple(int(part) for part in path.parents[2].name.split("."))
        except ValueError:
            return (0,)

    if candidates:
        return str(max(candidates, key=version))
    return ""


@dataclass(frozen=True)
class Settings:
    database_url: str
    capture_root: Path
    public_web_url: str
    reconstruction_root: Path = Path("data/reconstructions")
    colmap_executable: str = "colmap"
    openmvs_directory: str = ""
    session_minutes: int = 30
    max_image_bytes: int = 15 * 1024 * 1024
    max_video_bytes: int = 500 * 1024 * 1024
    max_session_bytes: int = 400 * 1024 * 1024
    min_capture_images: int = 30
    max_capture_images: int = 160
    ffmpeg_executable: str = "ffmpeg"

    @classmethod
    def from_environment(cls) -> "Settings":
        data_root = Path(os.getenv("GAUSSIANCIRCUIT_DATA_ROOT", "data")).resolve()
        return cls(
            database_url=os.getenv(
                "GAUSSIANCIRCUIT_DATABASE_URL",
                f"sqlite:///{(data_root / 'gaussiancircuit.db').as_posix()}",
            ),
            capture_root=Path(
                os.getenv("GAUSSIANCIRCUIT_CAPTURE_ROOT", data_root / "captures")
            ).resolve(),
            public_web_url=os.getenv("GAUSSIANCIRCUIT_WEB_URL", _local_web_url()).rstrip("/"),
            reconstruction_root=Path(
                os.getenv("GAUSSIANCIRCUIT_RECONSTRUCTION_ROOT", data_root / "reconstructions")
            ).resolve(),
            colmap_executable=os.getenv("GAUSSIANCIRCUIT_COLMAP", _project_colmap()),
            openmvs_directory=os.getenv("GAUSSIANCIRCUIT_OPENMVS", _project_openmvs()),
            ffmpeg_executable=os.getenv("GAUSSIANCIRCUIT_FFMPEG", shutil.which("ffmpeg") or "ffmpeg"),
        )
