"""Migración 0011 (notificaciones) sobre una base temporal: integra alerta_tecnica y se puede revertir."""

import pytest
from alembic import command
from alembic_pruebas import configuracion_alembic, insertar_reporte
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integracion

TABLAS = {"notificacion", "notificacion_usuario", "envio_correo"}


def _usuario(sesion: Session, nombre: str, rol: str, activo: bool = True) -> int:
    return sesion.scalar(
        text(
            "INSERT INTO usuario (nombre_usuario, nombre_completo, hash_contrasena, rol, activo) "
            "VALUES (:n, :n, 'x', :rol, :activo) RETURNING id"
        ),
        {"n": nombre, "rol": rol, "activo": activo},
    )


def _alerta(sesion: Session, jornada: str) -> None:
    sesion.execute(
        text(
            "INSERT INTO alerta_tecnica (tipo, fecha_jornada, mensaje, datos) "
            "VALUES ('JORNADA_SIN_CIERRE', :j, :m, '{\"alertas_pendientes\": 2}')"
        ),
        {"j": jornada, "m": f"ALERTA TÉCNICA: la jornada no tiene reporte de cierre {jornada}"},
    )


def test_la_0011_pasa_alerta_tecnica_a_notificaciones_de_los_admin_y_se_revierte(base_temporal):
    alembic = configuracion_alembic()
    command.upgrade(alembic, "0010")
    with Session(base_temporal) as sesion, sesion.begin():
        admin = _usuario(sesion, "admin", "ADMIN")
        _usuario(sesion, "admin-inactivo", "ADMIN", activo=False)
        _usuario(sesion, "contadora", "CONTADOR")
        _alerta(sesion, "2026-11-23")  # sin cierre: queda abierta
        _alerta(sesion, "2026-11-24")  # con cierre vigente: queda atendida
        insertar_reporte(sesion, "CIERRE", 1, 5)  # jornada 2026-11-24

    command.upgrade(alembic, "0011")
    inspector = inspect(base_temporal)
    assert TABLAS <= set(inspector.get_table_names())
    assert "alerta_tecnica" not in inspector.get_table_names()
    assert "email" in {c["name"] for c in inspector.get_columns("usuario")}
    with Session(base_temporal) as sesion:
        filas = sesion.execute(
            text(
                "SELECT n.fecha_jornada::text, n.tipo, n.clase, n.clave, n.atendida_en IS NOT NULL, u.usuario_id "
                "FROM notificacion n JOIN notificacion_usuario u ON u.notificacion_id = n.id ORDER BY 1"
            )
        ).all()
    assert filas == [
        ("2026-11-23", "JORNADA_SIN_CIERRE", "TECNICA", "jornada:2026-11-23", False, admin),
        ("2026-11-24", "JORNADA_SIN_CIERRE", "TECNICA", "jornada:2026-11-24", True, admin),
    ]

    command.downgrade(alembic, "0010")
    inspector = inspect(base_temporal)
    assert not TABLAS & set(inspector.get_table_names())
    with Session(base_temporal) as sesion:
        assert sesion.execute(text("SELECT fecha_jornada::text FROM alerta_tecnica ORDER BY 1")).scalars().all() == [
            "2026-11-23",
            "2026-11-24",
        ]
    command.upgrade(alembic, "head")


@pytest.fixture
def base(base_temporal):
    command.upgrade(configuracion_alembic(), "head")
    return base_temporal


def test_una_sola_notificacion_abierta_por_causa(base):
    insertar = text(
        "INSERT INTO notificacion (tipo, clase, clave, mensaje, atendida_en) "
        "VALUES ('ARCHIVO_NO_RECIBIDO', 'TECNICA', 'archivo:a.xml', 'x', :atendida)"
    )
    with Session(base) as sesion, sesion.begin():
        sesion.execute(insertar, {"atendida": "2026-11-25T10:00:00+00:00"})  # una atendida no cuenta
        sesion.execute(insertar, {"atendida": None})
    with pytest.raises(IntegrityError, match="uq_notificacion_causa_abierta"):
        with Session(base) as sesion, sesion.begin():
            sesion.execute(insertar, {"atendida": None})


def test_una_copia_por_usuario_y_estados_de_envio_validos(base):
    with Session(base) as sesion, sesion.begin():
        usuario = _usuario(sesion, "admin", "ADMIN")
        id_ = sesion.scalar(
            text(
                "INSERT INTO notificacion (tipo, clase, clave, mensaje) "
                "VALUES ('DESCUADRE', 'TECNICA', 'jornada:2026-11-25', 'x') RETURNING id"
            )
        )
        sesion.execute(
            text("INSERT INTO notificacion_usuario (notificacion_id, usuario_id) VALUES (:n, :u)"),
            {"n": id_, "u": usuario},
        )
    with pytest.raises(IntegrityError, match="notificacion_usuario_pkey"):
        with Session(base) as sesion, sesion.begin():
            sesion.execute(
                text("INSERT INTO notificacion_usuario (notificacion_id, usuario_id) VALUES (:n, :u)"),
                {"n": id_, "u": usuario},
            )
    with pytest.raises(IntegrityError, match="ck_envio_correo_estado"):
        with Session(base) as sesion, sesion.begin():
            sesion.execute(
                text(
                    "INSERT INTO envio_correo (notificacion_id, usuario_id, destinatario, estado) "
                    "VALUES (:n, :u, 'a@b.co', 'PERDIDA')"
                ),
                {"n": id_, "u": usuario},
            )
