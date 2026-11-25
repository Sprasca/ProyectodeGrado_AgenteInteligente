"""Notificaciones de la recepción automática (RF01): archivo no recibido, factura duplicada, directorio no
disponible y error inesperado. Sin base de datos: notificaciones en memoria."""

import logging
import shutil
import uuid

import pytest
from fabricas import xml_factura
from repositorio_notificaciones_memoria import RepositorioNotificacionesMemoria
from test_recepcion import AHORA, ProcesadorFalso, _dejar
from test_vigilante import ObservadorFalso

from agente.application.recepcion import ConfiguracionRecepcion, ReceptorFacturas
from agente.domain.enums import ClaseNotificacion, Rol, TipoNotificacion
from agente.domain.errors import FacturaDuplicadaError
from agente.domain.usuarios import Usuario
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada

ADMIN = Usuario(3, "admin", "Admin", Rol.ADMIN)
AUXILIAR = Usuario(2, "auxiliar", "Auxiliar", Rol.AUXILIAR)
T = TipoNotificacion


@pytest.fixture
def carpetas(tmp_path):
    entrada = tmp_path / "entrada"
    entrada.mkdir()
    return ConfiguracionRecepcion(
        entrada=entrada,
        recibidos=entrada / "recibidos",
        no_recibidos=entrada / "no_recibidos",
        tamano_maximo=1024 * 1024,
        espera_estabilidad=0,
        reintentos=0,
        espera_inicial=0,
    )


@pytest.fixture
def notificaciones():
    return RepositorioNotificacionesMemoria(lambda: [ADMIN, AUXILIAR])


def _receptor(carpetas, notificaciones, procesador=None):
    return ReceptorFacturas(
        carpetas,
        procesador or ProcesadorFalso(),
        dormir=lambda _: None,
        ahora=lambda: AHORA,
        notificar=notificaciones.registrar,
    )


# --- Receptor ------------------------------------------------------------------------------------------------


def test_un_archivo_invalido_notifica_al_admin_con_el_motivo(carpetas, notificaciones):
    _receptor(carpetas, notificaciones).recibir(_dejar(carpetas, "malo.xml", b"<no-es-factura/>"))
    (vista,) = notificaciones.listar(ADMIN.id)
    assert (vista.tipo, vista.clase) == (T.ARCHIVO_NO_RECIBIDO, ClaseNotificacion.TECNICA)
    assert vista.se_atiende_a_mano is True
    assert "malo.xml" in vista.mensaje and "no_recibidos" in vista.mensaje
    assert vista.datos["archivo"] == "malo.xml" and vista.datos["tipo_error"] == "DocumentoInvalidoError"
    assert notificaciones.listar(AUXILIAR.id) == []


def test_un_archivo_recibido_no_notifica(carpetas, notificaciones):
    _receptor(carpetas, notificaciones).recibir(_dejar(carpetas, "f.xml", xml_factura("FV-1")))
    assert notificaciones.listar(ADMIN.id) == notificaciones.listar(AUXILIAR.id) == []


def test_una_factura_duplicada_es_contable_y_va_al_auxiliar(carpetas, notificaciones):
    original = uuid.uuid4()
    procesador = ProcesadorFalso(FacturaDuplicadaError("La factura FV-1 ya fue registrada.", original))
    _receptor(carpetas, notificaciones, procesador).recibir(_dejar(carpetas, "f.xml", xml_factura("FV-1")))
    (vista,) = notificaciones.listar(AUXILIAR.id)
    assert (vista.tipo, vista.clase) == (T.FACTURA_DUPLICADA, ClaseNotificacion.CONTABLE)
    assert str(original) in vista.mensaje and "dos veces" in vista.mensaje
    assert notificaciones.listar(ADMIN.id) == []


def test_dos_archivos_con_el_mismo_nombre_son_dos_notificaciones(carpetas, notificaciones):
    receptor = _receptor(carpetas, notificaciones)
    receptor.recibir(_dejar(carpetas, "malo.xml", b"<uno/>"))
    receptor.recibir(_dejar(carpetas, "malo.xml", b"<dos/>"))
    assert len(notificaciones.listar(ADMIN.id)) == 2


def test_si_no_se_puede_notificar_el_archivo_igual_va_a_no_recibidos(carpetas, caplog):
    def falla(_):
        raise RuntimeError("sin base")

    receptor = ReceptorFacturas(
        carpetas, ProcesadorFalso(), dormir=lambda _: None, ahora=lambda: AHORA, notificar=falla
    )
    with caplog.at_level(logging.ERROR, logger="agente.recepcion"):
        recepcion = receptor.recibir(_dejar(carpetas, "malo.xml", b"<no-es-factura/>"))
    assert not recepcion.recibido and (carpetas.no_recibidos / "malo.xml").exists()
    assert "No se pudo registrar la notificación ARCHIVO_NO_RECIBIDO" in caplog.text


# --- Vigilante -----------------------------------------------------------------------------------------------


@pytest.fixture
def vigilante(carpetas, notificaciones):
    ObservadorFalso.creados = []
    return VigilanteEntrada(
        ReceptorFacturas(carpetas, ProcesadorFalso(), dormir=lambda _: None),
        crear_observador=ObservadorFalso,
        notificaciones=notificaciones,
    )


def test_el_directorio_no_disponible_notifica_una_vez_y_queda_atendido_al_volver(vigilante, carpetas, notificaciones):
    vigilante.revisar()
    shutil.rmtree(carpetas.entrada)
    vigilante.revisar()
    vigilante.revisar()  # sigue caído: la misma notificación
    (vista,) = notificaciones.listar(ADMIN.id)
    assert vista.tipo is T.DIRECTORIO_NO_DISPONIBLE and str(carpetas.entrada) in vista.mensaje
    assert vista.se_atiende_a_mano is False and vista.atendida_en is None

    carpetas.entrada.mkdir()
    vigilante.revisar()
    (vista,) = notificaciones.listar(ADMIN.id)
    assert vista.atendida_en is not None and vista.resolucion == "El directorio de entrada volvió a estar disponible."

    shutil.rmtree(carpetas.entrada)  # una nueva caída es otra notificación
    vigilante.revisar()
    assert len(notificaciones.listar(ADMIN.id, no_atendidas=True)) == 1
    assert len(notificaciones.listar(ADMIN.id)) == 2


def test_un_error_inesperado_al_recibir_notifica_al_admin(vigilante, carpetas, notificaciones, monkeypatch):
    def falla(_):
        raise RuntimeError("fallo del receptor")

    monkeypatch.setattr(vigilante.receptor, "recibir", falla)
    vigilante.encolar(_dejar(carpetas, "f.xml", xml_factura("FV-1")))
    vigilante.atender_cola()
    (vista,) = notificaciones.listar(ADMIN.id)
    assert vista.tipo is T.ERROR_RECEPCION and "fallo del receptor" in vista.mensaje and vista.se_atiende_a_mano
