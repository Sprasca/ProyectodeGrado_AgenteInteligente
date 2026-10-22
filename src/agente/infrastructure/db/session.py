from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from ...config import get_settings


def crear_sessionmaker(url: str | None = None) -> sessionmaker[Session]:
    engine = create_engine(url or get_settings().database_url, pool_pre_ping=True)
    return sessionmaker(engine, expire_on_commit=False)
