"""Recepción automática de punta a punta (RF01) con PostgreSQL real y watchdog real.

Se deja un XML en una carpeta de entrada temporal y se comprueba que termina en recibidos/ y que su
transacción, el documento original y los eventos (sin usuario, canal "carpeta") quedan en la base.
Las facturas usan números únicos ITEST-RECEP-... y se borran al terminar.
"""

import hashlib
import importlib.util
import json
import logging
import threading
import time
import uuid
from pathlib import Path

import pytest
from fabricas import cufe_unico, xml_factura
from limpieza_bd import borrar_transacciones
from sqlalchemy import select

from agente.application.clasificar_con_nlp import invalidar_catalogo
from agente.application.recepcion import (
    SUFIJO_MOTIVO,
    ConfiguracionRecepcion,
    receptor_por_defecto,
)
from agente.config import get_settings
from agente.infrastructure.db.models import DocumentoModel, EventoTransaccionModel, TransaccionModel
from agente.infrastructure.recepcion.vigilante import VigilanteEntrada

pytestmark = pytest.mark.integracion

PREFIJO = "ITEST-RECEP-"


@pytest.fixture
def entrada(tmp_path, monkeypatch, fabrica_bd):
    """Carpeta de entrada temporal, catálogo desde la base, y limpieza de las transacciones creadas."""
    raiz = tmp_path / "entrada"
    raiz.mkdir()
    monkeypatch.setenv("FUENTE_CATALOGO", "bd")
    monkeypatch.setenv("DIRECTORIO_ENTRADA", str(raiz))
    monkeypatch.delenv("DIRECTORIO_RECIBIDOS", raising=False)
    monkeypatch.delenv("DIRECTORIO_NO_RECIBIDOS", raising=False)
    monkeypatch.setenv("RECEPCION_ESPERA_ESTABILIDAD", "0.2")
    get_settings.cache_clear()
    invalidar_catalogo()
    yield raiz
    with fabrica_bd() as sesion, sesion.begin():
        borrar_transacciones(sesion, TransaccionModel.numero_factura.like(f"{PREFIJO}%"))
    get_settings.cache_clear()
    invalidar_catalogo()


def _configuracion(raiz):
    return ConfiguracionRecepcion(
        entrada=raiz,
        recibidos=raiz / "recibidos",
        no_recibidos=raiz / "no_recibidos",
        tamano_maximo=get_settings().tamano_maximo_documento,
        espera_estabilidad=0.2,
        reintentos=0,
        espera_inicial=0,
    )


def _numero():
    return f"{PREFIJO}{uuid.uuid4().hex[:10]}"


def _esperar(condicion, segundos=20):
    limite = time.monotonic() + segundos
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.1)
    return condicion()


@pytest.fixture
def vigilante_en_marcha(entrada):
    """El vigilante real (watchdog) en un hilo, con el mismo procesador que usa scripts/recibir_facturas.py."""
    vigilante = VigilanteEntrada(receptor_por_defecto(_configuracion(entrada), usar_nlp=False), intervalo=1)
    detener = threading.Event()
    hilo = threading.Thread(target=vigilante.ejecutar, args=(detener,), daemon=True)
    hilo.start()
    time.sleep(0.5)  # que el observador arranque
    yield entrada
    detener.set()
    hilo.join(timeout=30)


def test_un_xml_dejado_en_la_carpeta_queda_registrado_y_en_recibidos(vigilante_en_marcha, fabrica_bd):
    entrada = vigilante_en_marcha
    numero, cufe = _numero(), cufe_unico()
    contenido = xml_factura(numero, cufe)
    (entrada / "factura-sistema-externo.xml").write_bytes(contenido)

    recibido = entrada / "recibidos" / "factura-sistema-externo.xml"
    assert _esperar(recibido.exists), "El archivo no llegó a recibidos/"
    assert recibido.read_bytes() == contenido
    assert not (entrada / "factura-sistema-externo.xml").exists()

    with fabrica_bd() as sesion:
        transaccion = sesion.scalar(select(TransaccionModel).where(TransaccionModel.numero_factura == numero))
        assert transaccion is not None, "La transacción no está en la base"
        assert transaccion.cufe == cufe
        assert transaccion.estado == "APROBADA_AUTOMATICA"
        documento = sesion.get(DocumentoModel, transaccion.id)
        assert documento.contenido == contenido
        assert documento.sha256 == hashlib.sha256(contenido).hexdigest()
        assert documento.nombre_archivo == "factura-sistema-externo.xml"
        eventos = sesion.scalars(
            select(EventoTransaccionModel)
            .where(EventoTransaccionModel.transaccion_id == transaccion.id)
            .order_by(EventoTransaccionModel.id)
        ).all()
    assert [(e.tipo, e.usuario_id) for e in eventos] == [("RECIBIDA", None), ("CLASIFICADA", None)]
    assert eventos[0].despues["canal"] == "carpeta"
    assert eventos[0].despues["documento"]["nombre_archivo"] == "factura-sistema-externo.xml"


def test_xml_invalido_y_duplicado_van_a_no_recibidos(vigilante_en_marcha, fabrica_bd):
    entrada = vigilante_en_marcha
    numero = _numero()
    contenido = xml_factura(numero)

    (entrada / "original.xml").write_bytes(contenido)
    assert _esperar((entrada / "recibidos" / "original.xml").exists)

    # El mismo archivo otra vez, con otro nombre: duplicado.
    (entrada / "copia.xml").write_bytes(contenido)
    # Un XML que no es una factura.
    (entrada / "no-es-factura.xml").write_bytes(b"<Pedido><Numero>1</Numero></Pedido>")

    no_recibidos = entrada / "no_recibidos"
    assert _esperar(lambda: (no_recibidos / "copia.xml").exists() and (no_recibidos / "no-es-factura.xml").exists())
    duplicado = json.loads((no_recibidos / ("copia.xml" + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))
    assert duplicado["tipo_error"] == "FacturaDuplicadaError"
    invalido = json.loads((no_recibidos / ("no-es-factura.xml" + SUFIJO_MOTIVO)).read_text(encoding="utf-8"))
    assert invalido["tipo_error"] == "DocumentoInvalidoError"

    with fabrica_bd() as sesion:
        cuantas = sesion.scalars(select(TransaccionModel.id).where(TransaccionModel.numero_factura == numero)).all()
    assert len(cuantas) == 1


def test_script_una_vez_procesa_lo_que_ya_estaba_en_la_carpeta(entrada, fabrica_bd):
    numero = _numero()
    (entrada / "pendiente.xml").write_bytes(xml_factura(numero))

    ruta = Path(__file__).resolve().parents[2] / "scripts" / "recibir_facturas.py"
    spec = importlib.util.spec_from_file_location("recibir_facturas", ruta)
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    assert script.main(["--una-vez", "--sin-nlp"]) == 0

    assert (entrada / "recibidos" / "pendiente.xml").exists()
    with fabrica_bd() as sesion:
        assert sesion.scalar(select(TransaccionModel.id).where(TransaccionModel.numero_factura == numero))


def test_factura_registrada_que_no_se_alcanzo_a_mover_va_a_recibidos_sin_otra_transaccion(
    entrada, fabrica_bd, caplog, monkeypatch
):
    """Caso límite: la factura quedó registrada pero el archivo no se pudo mover (sigue en entrada/)."""
    import agente.application.recepcion as recepcion

    numero = _numero()
    contenido = xml_factura(numero)
    receptor = receptor_por_defecto(_configuracion(entrada), usar_nlp=False)
    ruta = entrada / "sin-mover.xml"
    ruta.write_bytes(contenido)

    mover = recepcion.shutil.move
    monkeypatch.setattr(recepcion.shutil, "move", lambda *_: (_ for _ in ()).throw(PermissionError("bloqueado")))
    receptor.recibir(ruta)
    assert ruta.exists() and not (entrada / "recibidos" / "sin-mover.xml").exists()
    monkeypatch.setattr(recepcion.shutil, "move", mover)

    caplog.clear()  # el ERROR del movimiento fallido es el que se simuló
    with caplog.at_level(logging.WARNING, logger="agente.recepcion"):
        recuperada = receptor.recibir(ruta)
    assert recuperada.recibido and recuperada.destino == entrada / "recibidos" / "sin-mover.xml"
    assert not (entrada / "no_recibidos").exists()
    assert any(r.levelno == logging.WARNING and "ya estaba registrada" in r.getMessage() for r in caplog.records)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    with fabrica_bd() as sesion:
        ids = sesion.scalars(select(TransaccionModel.id).where(TransaccionModel.numero_factura == numero)).all()
    assert len(ids) == 1 and recuperada.id_transaccion == ids[0]


def test_el_mismo_archivo_copiado_dos_veces_seguidas_termina_en_recibidos_y_en_no_recibidos(
    vigilante_en_marcha, fabrica_bd
):
    """Lo que se reportó: dos copias seguidas de FV-2001.xml en entrada/ (vigilante y base reales)."""
    entrada = vigilante_en_marcha
    numero = _numero()
    contenido = xml_factura(numero)
    (entrada / "FV-2001.xml").write_bytes(contenido)
    assert _esperar((entrada / "recibidos" / "FV-2001.xml").exists)
    (entrada / "FV-2001.xml").write_bytes(contenido)
    no_recibido = entrada / "no_recibidos" / "FV-2001.xml"
    assert _esperar(lambda: no_recibido.exists() and Path(str(no_recibido) + SUFIJO_MOTIVO).exists())

    assert [p.name for p in (entrada / "recibidos").iterdir()] == ["FV-2001.xml"]
    motivo = json.loads(Path(str(no_recibido) + SUFIJO_MOTIVO).read_text(encoding="utf-8"))
    assert motivo["tipo_error"] == "FacturaDuplicadaError"
    with fabrica_bd() as sesion:
        ids = sesion.scalars(select(TransaccionModel.id).where(TransaccionModel.numero_factura == numero)).all()
    assert len(ids) == 1
    # El contador ve cuál es la transacción original.
    assert motivo["id_transaccion_existente"] == str(ids[0])
    assert motivo["motivo"] == f"La factura {numero} ya fue registrada en la transacción {ids[0]}."
