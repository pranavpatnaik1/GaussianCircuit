from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import Settings


class Base(DeclarativeBase):
    pass


class Database:
    def __init__(self, settings: Settings):
        connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
        self.engine = create_engine(settings.database_url, connect_args=connect_args)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def create_schema(self) -> None:
        Base.metadata.create_all(self.engine)

    def dependency(self) -> Generator[Session, None, None]:
        with self.sessions() as session:
            yield session
