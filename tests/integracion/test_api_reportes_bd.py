"""Ciclo completo de reportes por la API con PostgreSQL real, sobre una base temporal (ver bd_sembrada).

Consolidar (auxiliar) → exportar (contador) → descargar el XML (auxiliar) → exportar otra vez (409)
→ corregir una transacción exportada (409). Se verifica además lo que quedó en las tablas.
"""

import hashlib

import pytest
from autenticacion_pruebas import CONTRASENA
from fabricas import partidas_correctas, resultado_aprobado
from fastapi.testclient import TestClient
from sqlalchemy import select

from agente.api.autenticacion import crear_token, get_repositorio_usuarios
from agente.api.main import app, get_catalogo, get_repositorio, get_repositorio_reportes
from agente.domain.enums import Rol
from agente.infrastructure.db.models import (
    EventoReporteModel,
    EventoTransaccionModel,
    ReporteDiarioModel,
    ReporteTransaccionModel,
)
from agente.infrastructure.db.reportes import RepositorioReportesSQL
from agente.infrastructure.db.transacciones import RepositorioSQL
from agente.infrastructure.db.usuarios import RepositorioUsuariosSQL

pytestmark = pytest.mark.integracion


@pytest.fixture
def api_bd(bd_sembrada, catalogo):
    transacciones = RepositorioSQL(bd_sembrada, catalogo)
    usuarios = RepositorioUsuariosSQL(bd_sembrada)
    creados = {
        Rol.CONTADOR: usuarios.crear("contadora", "Contadora", CONTRASENA, Rol.CONTADOR),
        Rol.AUXILIAR: usuarios.crear("auxiliar", "Auxiliar", CONTRASENA, Rol.AUXILIAR),
    }
    app.dependency_overrides[get_repositorio] = lambda: transacciones
    app.dependency_overrides[get_repositorio_reportes] = lambda: RepositorioReportesSQL(bd_sembrada)
    app.dependency_overrides[get_repositorio_usuarios] = lambda: usuarios
    app.dependency_overrides[get_catalogo] = lambda: catalogo
    try:
        yield TestClient(app), transacciones, creados, bd_sembrada
    finally:
        app.dependency_overrides.clear()


def test_ciclo_completo_consolidar_exportar_descargar(api_bd, catalogo):
    cliente, transacciones, usuarios, fabrica = api_bd
    como = {rol: {"Authorization": f"Bearer {crear_token(u)}"} for rol, u in usuarios.items()}
    ids = [transacciones.guardar(resultado_aprobado(catalogo, f"FV-BD-{n}")) for n in (1, 2)]

    consolidado = cliente.post("/reportes/consolidar", headers=como[Rol.AUXILIAR])
    assert consolidado.status_code == 201, consolidado.text
    reporte = consolidado.json()

    exportado = cliente.post(f"/reportes/{reporte['id']}/exportar", headers=como[Rol.CONTADOR])
    assert exportado.status_code == 200, exportado.text
    xml = cliente.get(f"/reportes/{reporte['id']}/xml", headers=como[Rol.AUXILIAR])
    assert xml.status_code == 200
    assert hashlib.sha256(xml.content).hexdigest() == exportado.json()["archivo_sha256"]

    otra_vez = cliente.post(f"/reportes/{reporte['id']}/exportar", headers=como[Rol.CONTADOR])
    assert otra_vez.status_code == 409
    partidas = [
        {"cuenta": p.cuenta, "naturaleza": p.naturaleza.value, "valor": str(p.valor)}
        for p in partidas_correctas(catalogo)
    ]
    corregir = cliente.post(
        f"/transacciones/{ids[0]}/corregir",
        json={"partidas": partidas, "motivo": "Corrección después de exportar"},
        headers=como[Rol.CONTADOR],
    )
    assert corregir.status_code == 409

    with fabrica() as sesion:
        fila = sesion.get(ReporteDiarioModel, reporte["id"])
        assert fila.estado == "EXPORTADO" and fila.archivo_xml == xml.content
        assert fila.exportado_por == usuarios[Rol.CONTADOR].id and fila.exportado_en is not None
        vinculos = set(
            sesion.scalars(
                select(ReporteTransaccionModel.transaccion_id).where(ReporteTransaccionModel.reporte_id == fila.id)
            )
        )
        assert vinculos == set(ids)
        eventos = sesion.scalars(
            select(EventoReporteModel).where(EventoReporteModel.reporte_id == fila.id).order_by(EventoReporteModel.id)
        ).all()
        assert [(e.tipo, e.usuario_id) for e in eventos] == [
            ("CONSOLIDADO", usuarios[Rol.AUXILIAR].id),
            ("EXPORTADO", usuarios[Rol.CONTADOR].id),
        ]
        assert eventos[1].despues["sha256"] == fila.archivo_sha256
        tipos = sesion.scalars(
            select(EventoTransaccionModel.tipo)
            .where(EventoTransaccionModel.transaccion_id == ids[0])
            .order_by(EventoTransaccionModel.id)
        ).all()
        assert tipos == ["RECIBIDA", "CLASIFICADA", "CONSOLIDADA"]  # la corrección rechazada no dejó evento
