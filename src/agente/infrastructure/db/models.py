import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import ARRAY, JSON, Boolean, Date, DateTime, ForeignKey, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 384  # modelo multilingüe pequeño local (Sprint 2)


class Base(DeclarativeBase):
    pass


class CuentaPUCModel(Base):
    __tablename__ = "cuenta_puc"

    codigo: Mapped[str] = mapped_column(String(10), primary_key=True)
    nombre: Mapped[str] = mapped_column(String(200))
    naturaleza: Mapped[str] = mapped_column(String(10))


class ReglaClasificacionModel(Base):
    __tablename__ = "regla_clasificacion"

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), unique=True)
    cuenta_codigo: Mapped[str] = mapped_column(ForeignKey("cuenta_puc.codigo"))
    palabras_clave: Mapped[list[str]] = mapped_column(ARRAY(String))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    # Se llena en el Sprint 2 (RAG sobre el PUC)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)


class TransaccionModel(Base):
    __tablename__ = "transaccion"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    numero_factura: Mapped[str] = mapped_column(String(60), unique=True)
    fecha: Mapped[date] = mapped_column(Date)
    nit_adquirente: Mapped[str] = mapped_column(String(30))
    valor_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    estado: Mapped[str] = mapped_column(String(30))
    confianza: Mapped[Decimal] = mapped_column(Numeric(4, 3))
    reglas_aplicadas: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    motivos_alerta: Mapped[list[str]] = mapped_column(JSON, default=list)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    partidas: Mapped[list["PartidaModel"]] = relationship(
        back_populates="transaccion", cascade="all, delete-orphan"
    )


class PartidaModel(Base):
    __tablename__ = "partida"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transaccion.id", ondelete="CASCADE"), index=True
    )
    cuenta_codigo: Mapped[str] = mapped_column(ForeignKey("cuenta_puc.codigo"))
    naturaleza: Mapped[str] = mapped_column(String(10))
    valor: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    descripcion: Mapped[str] = mapped_column(String(200), default="")

    transaccion: Mapped[TransaccionModel] = relationship(back_populates="partidas")