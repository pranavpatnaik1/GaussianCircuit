from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .captures import build_router
from .config import Settings
from .database import Database


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_environment()
    database = Database(settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        settings.capture_root.mkdir(parents=True, exist_ok=True)
        database.create_schema()
        try:
            yield
        finally:
            database.engine.dispose()

    app = FastAPI(title="GaussianCircuit API", version="0.2.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.database = database
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.public_web_url],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(build_router(settings, database.dependency), prefix="/api")

    @app.get("/api/health")
    def health():
        return {"status": "ok"}

    return app


app = create_app()
