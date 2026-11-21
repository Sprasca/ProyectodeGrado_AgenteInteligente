import uuid
from datetime import date, datetime
from decimal import Decimal

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    ARRAY,
    JSON,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Text,
    func,
    text,
)
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
    __table_args__ = (CheckConstraint("origen IN ('csv', 'contador')", name="ck_regla_clasificacion_origen"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    codigo: Mapped[str] = mapped_column(String(60), unique=True)
    cuenta_codigo: Mapped[str] = mapped_column(ForeignKey("cuenta_puc.codigo"))
    palabras_clave: Mapped[list[str]] = mapped_column(ARRAY(String))
    descripcion: Mapped[str] = mapped_column(Text, default="")
    activa: Mapped[bool] = mapped_column(Boolean, default=True)
    # Se llena en el Sprint 2 (RAG sobre el PUC)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    actualizado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # "csv": la creó sembrar_puc.py desde data/puc; "contador": la creó el contador por la API.
    origen: Mapped[str] = mapped_column(String(10), server_default=text("'csv'"))


class HistorialReglaModel(Base):
    """Un cambio de una regla (RF09): quién, cuándo, qué acción y el antes/después."""

    __tablename__ = "historial_regla"
    __table_args__ = (
        CheckConstraint(
            "accion IN ('CREAR', 'EDITAR', 'ACTIVAR', 'DESACTIVAR')", name="ck_historial_regla_accion"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    regla_codigo: Mapped[str] = mapped_column(
        ForeignKey("regla_clasificacion.codigo", name="fk_historial_regla_codigo"), index=True
    )
    accion: Mapped[str] = mapped_column(String(12))
    antes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    despues: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    usuario: Mapped[str | None] = mapped_column(Text, nullable=True)
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


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
    detalle_lineas: Mapped[list[dict]] = mapped_column(JSON, default=list, server_default=text("'[]'"))
    # Partidas que propuso el agente; se llena en la primera corrección del contador.
    asiento_propuesto: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)
    corregido_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    partidas: Mapped[list["PartidaModel"]] = relationship(back_populates="transaccion", cascade="all, delete-orphan")


class PartidaModel(Base):
    __tablename__ = "partida"

    id: Mapped[int] = mapped_column(primary_key=True)
    transaccion_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("transaccion.id", ondelete="CASCADE"), index=True)
    cuenta_codigo: Mapped[str] = mapped_column(ForeignKey("cuenta_puc.codigo"))
    naturaleza: Mapped[str] = mapped_column(String(10))
    valor: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    descripcion: Mapped[str] = mapped_column(String(200), default="")

    transaccion: Mapped[TransaccionModel] = relationship(back_populates="partidas")


class UsuarioModel(Base):
    __tablename__ = "usuario"
    __table_args__ = (CheckConstraint("rol IN ('CONTADOR', 'AUXILIAR', 'ADMIN')", name="ck_usuario_rol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    nombre_usuario: Mapped[str] = mapped_column(String(60), unique=True)
    nombre_completo: Mapped[str] = mapped_column(String(200))
    hash_contrasena: Mapped[str] = mapped_column(Text)  # Argon2id
    rol: Mapped[str] = mapped_column(String(10))
    activo: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
