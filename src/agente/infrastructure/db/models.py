import uuid
from datetime import date, datetime, time
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
    Index,
    LargeBinary,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
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
    __table_args__ = (UniqueConstraint("cufe", name="uq_transaccion_cufe"),)

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
    # CUFE de la DIAN (cbc:UUID). Único: la misma factura no se registra dos veces.
    cufe: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Reporte diario vigente (RF06): uno como máximo. Se libera (NULL) si el reporte se anula.
    reporte_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("reporte_diario.id", name="fk_transaccion_reporte"), nullable=True, index=True
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Jornada contable a la que pertenece: la del día si llegó antes de la HORA_CIERRE de Colombia, si no la
    # del día siguiente. Se fija al recibirla: cambiar el horario no la mueve.
    fecha_jornada: Mapped[date] = mapped_column(Date, index=True)

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


class EventoTransaccionModel(Base):
    """Un hecho en la vida de una transacción: quién, cuándo, por qué y el antes/después (no repudio)."""

    __tablename__ = "evento_transaccion"
    __table_args__ = (
        CheckConstraint(
            "tipo IN ('RECIBIDA', 'CLASIFICADA', 'APROBADA', 'CORREGIDA', 'RECHAZADA', 'CONSOLIDADA', 'LIBERADA', "
            "'EXPORTADA')",
            name="ck_evento_transaccion_tipo",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transaccion.id", name="fk_evento_transaccion_transaccion"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(12))
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", name="fk_evento_transaccion_usuario"), nullable=True
    )
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    antes: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    despues: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class DocumentoModel(Base):
    """El archivo original (XML o PDF) tal como se recibió, con su huella SHA-256."""

    __tablename__ = "documento"
    __table_args__ = (
        CheckConstraint("tipo IN ('XML', 'PDF')", name="ck_documento_tipo"),
        UniqueConstraint("sha256", name="uq_documento_sha256"),  # el mismo archivo no se registra dos veces
    )

    transaccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transaccion.id", name="fk_documento_transaccion"), primary_key=True
    )
    nombre_archivo: Mapped[str] = mapped_column(String(255))
    tipo: Mapped[str] = mapped_column(String(3))
    contenido: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64))
    recibido_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReporteDiarioModel(Base):
    """
    Reporte de transacciones diarias (RF06) y, cuando se exporta, el XML generado (RF07).

    Hasta 5 reportes vigentes (no ANULADOS) por jornada, impuesto por la base con el `cupo`: los PARCIALES
    ocupan los cupos 1 a 4 y el de CIERRE el 5, y un cupo no se repite entre los vigentes de una jornada.
    Así el límite de parciales nunca impide cerrar el día y hay un solo cierre vigente por jornada.
    `numero` es el consecutivo dentro de la jornada: cuenta también los anulados y no se reutiliza.
    """

    __tablename__ = "reporte_diario"
    __table_args__ = (
        CheckConstraint("estado IN ('CONSOLIDADO', 'EXPORTADO', 'ANULADO')", name="ck_reporte_diario_estado"),
        # Solo un reporte EXPORTADO tiene archivo, y todo reporte EXPORTADO lo tiene.
        CheckConstraint(
            "(estado = 'EXPORTADO') = (archivo_xml IS NOT NULL AND archivo_sha256 IS NOT NULL)",
            name="ck_reporte_diario_exportado_con_archivo",
        ),
        CheckConstraint("tipo IN ('PARCIAL', 'CIERRE')", name="ck_reporte_diario_tipo"),
        CheckConstraint(
            "(tipo = 'PARCIAL' AND cupo BETWEEN 1 AND 4) OR (tipo = 'CIERRE' AND cupo = 5)",
            name="ck_reporte_diario_cupo",
        ),
        CheckConstraint("numero >= 1", name="ck_reporte_diario_numero"),
        UniqueConstraint("fecha_jornada", "numero", name="uq_reporte_diario_jornada_numero"),
        # Máximo 5 vigentes por jornada (4 parciales y el cierre): un cupo no se repite entre los vigentes.
        Index(
            "uq_reporte_diario_cupo_vigente",
            "fecha_jornada",
            "cupo",
            unique=True,
            postgresql_where=text("estado <> 'ANULADO'"),
        ),
        # Un solo reporte de cierre vigente por jornada (lo implica el cupo 5; se declara explícito).
        Index(
            "uq_reporte_diario_cierre_vigente",
            "fecha_jornada",
            unique=True,
            postgresql_where=text("tipo = 'CIERRE' AND estado <> 'ANULADO'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    fecha_jornada: Mapped[date] = mapped_column(Date, index=True)
    tipo: Mapped[str] = mapped_column(String(7))
    numero: Mapped[int]
    cupo: Mapped[int] = mapped_column(SmallInteger)
    corte: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    estado: Mapped[str] = mapped_column(String(12))
    numero_transacciones: Mapped[int]
    total_debito: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    total_credito: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    valor_total: Mapped[Decimal] = mapped_column(Numeric(18, 2))
    # Totales por código registrado y subtotales por cuenta de 4 dígitos (se conservan si se anula).
    movimientos: Mapped[list[dict]] = mapped_column(JSON)
    subtotales: Mapped[list[dict]] = mapped_column(JSON)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archivo_xml: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    archivo_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    exportado_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    exportado_por: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", name="fk_reporte_diario_exportado_por"), nullable=True
    )


class ReporteTransaccionModel(Base):
    """Qué transacciones se consolidaron en cada reporte. Histórica: se conserva aunque el reporte se anule."""

    __tablename__ = "reporte_transaccion"

    reporte_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reporte_diario.id", name="fk_reporte_transaccion_reporte"), primary_key=True
    )
    transaccion_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("transaccion.id", name="fk_reporte_transaccion_transaccion"), primary_key=True, index=True
    )


class EventoReporteModel(Base):
    """Cada cambio de estado de un reporte: quién (NULL si fue el cierre programado), cuándo y por qué."""

    __tablename__ = "evento_reporte"
    __table_args__ = (
        CheckConstraint("tipo IN ('CONSOLIDADO', 'EXPORTADO', 'ANULADO')", name="ck_evento_reporte_tipo"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    reporte_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("reporte_diario.id", name="fk_evento_reporte_reporte"), index=True
    )
    tipo: Mapped[str] = mapped_column(String(12))
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", name="fk_evento_reporte_usuario"), nullable=True
    )
    fecha: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    motivo: Mapped[str | None] = mapped_column(Text, nullable=True)
    despues: Mapped[dict | None] = mapped_column(JSON, nullable=True)


class HorarioJornadaModel(Base):
    """
    Horario de la jornada que define el contador. Solo se agregan filas: la vigente es la de mayor id y las
    anteriores registran quién cambió el horario y cuándo (usuario NULL: el valor inicial 08:00-18:00).
    """

    __tablename__ = "horario_jornada"
    __table_args__ = (CheckConstraint("hora_apertura < hora_cierre", name="ck_horario_jornada_apertura_cierre"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    hora_apertura: Mapped[time] = mapped_column(Time)
    hora_cierre: Mapped[time] = mapped_column(Time)
    usuario_id: Mapped[int | None] = mapped_column(
        ForeignKey("usuario.id", name="fk_horario_jornada_usuario"), nullable=True
    )
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertaTecnicaModel(Base):
    """
    Alertas técnicas para el responsable; hoy, la jornada que quedó sin reporte de cierre. La Fase 5 las
    convertirá en notificaciones y llenará notificada_en. Una por tipo y jornada: el cierre programado hace
    un solo intento y esta fila deja constancia de que ya lo hizo.
    """

    __tablename__ = "alerta_tecnica"
    __table_args__ = (
        CheckConstraint("tipo IN ('JORNADA_SIN_CIERRE')", name="ck_alerta_tecnica_tipo"),
        UniqueConstraint("tipo", "fecha_jornada", name="uq_alerta_tecnica_tipo_jornada"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tipo: Mapped[str] = mapped_column(String(30))
    fecha_jornada: Mapped[date | None] = mapped_column(Date, nullable=True)
    mensaje: Mapped[str] = mapped_column(Text)
    datos: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    creado_en: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    notificada_en: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
