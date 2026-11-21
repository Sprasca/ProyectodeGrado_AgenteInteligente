"""Pruebas de contrato de RepositorioTransacciones.

Las heredan test_repositorio_memoria.py (unitarias) y test_repositorio_sql.py (integración);
cada módulo define los fixtures `repo`, `nuevo_numero` (genera números de factura únicos) y
`repo_extendido` (su catálogo tiene CUENTA_NO_SEMBRADA, que la base no tiene).
El `repo` de integración debe borrar al final todo lo que guardó, incluidas las facturas
con número provisional (SIN-NUMERO-...), que no usan `nuevo_numero`.
"""

import re
import uuid
from decimal import Decimal

import pytest
from fabricas import (
    CUENTA_NO_SEMBRADA,
    partidas_con_cuenta_no_sembrada,
    partidas_correctas,
    resultado_alerta_con_asiento,
    resultado_alerta_sin_asiento,
    resultado_aprobado,
    resultado_con_cuenta_no_sembrada,
    resultado_sin_numero,
)

from agente.domain.asiento import Partida
from agente.domain.enums import EstadoTransaccion, Naturaleza
from agente.domain.errors import (
    AsientoInvalidoError,
    CuentaNoExisteError,
    FacturaDuplicadaError,
    TransaccionNoEncontradaError,
    TransicionInvalidaError,
)
from agente.domain.factura import MOTIVO_SIN_NUMERO
from agente.infrastructure.db.transacciones import CuentaNoSembradaError


def _partidas_con_bancos(catalogo):
    return [
        Partida(catalogo.resolver("bancos"), Naturaleza.DEBITO, Decimal("119000.00"), "Cobro por banco"),
        Partida("4135", Naturaleza.CREDITO, Decimal("100000.00"), "Ingreso"),
        Partida(catalogo.cuenta_iva(), Naturaleza.CREDITO, Decimal("19000.00"), "IVA"),
    ]


class ContratoRepositorio:
    # --- guardar / obtener / listar -------------------------------------------------

    def test_guardar_y_obtener_con_partidas(self, repo, nuevo_numero, catalogo):
        numero = nuevo_numero()
        resultado = resultado_aprobado(catalogo, numero)
        id_ = repo.guardar(resultado)

        t = repo.obtener(id_)
        assert t is not None
        assert t.id == id_
        assert t.numero_factura == numero
        assert t.fecha == resultado.factura.fecha
        assert t.nit_adquirente == "800987654"
        assert t.valor_total == Decimal("119000")
        assert t.estado is EstadoTransaccion.APROBADA_AUTOMATICA
        assert t.confianza == Decimal("1.000")
        assert t.reglas_aplicadas == ("R-4135-COMERCIO",)
        assert t.motivos_alerta == ()
        assert len(t.partidas) == 3
        assert {(p.cuenta, p.naturaleza, p.valor) for p in t.partidas} == {
            (p.cuenta, p.naturaleza, p.valor) for p in resultado.asiento.partidas
        }
        assert len(t.detalle_lineas) == 1
        linea = t.detalle_lineas[0]
        assert linea["estado"] == "CLASIFICADA"
        assert linea["cuenta_ingreso"] == "4135"
        assert linea["palabras_encontradas"] == ["mercancia", "producto"]

    def test_guardar_sin_asiento_no_crea_partidas(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        t = repo.obtener(id_)
        assert t.estado is EstadoTransaccion.EN_ALERTA
        assert t.partidas == ()
        assert t.motivos_alerta
        assert t.detalle_lineas[0]["estado"] == "SIN_REGLA"

    def test_guardar_duplicado_lanza_error_y_no_guarda(self, repo, nuevo_numero, catalogo):
        numero = nuevo_numero()
        repo.guardar(resultado_aprobado(catalogo, numero))
        with pytest.raises(FacturaDuplicadaError):
            repo.guardar(resultado_alerta_sin_asiento(catalogo, numero))
        mismas = [t for t in repo.listar() if t.numero_factura == numero]
        assert len(mismas) == 1
        assert mismas[0].estado is EstadoTransaccion.APROBADA_AUTOMATICA

    def test_obtener_inexistente_devuelve_none(self, repo):
        assert repo.obtener(uuid.uuid4()) is None

    def test_listar_filtra_por_estado(self, repo, nuevo_numero, catalogo):
        id_aprobada = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        id_alerta = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))

        todas = {t.id for t in repo.listar()}
        assert {id_aprobada, id_alerta} <= todas

        en_alerta = repo.listar(EstadoTransaccion.EN_ALERTA)
        assert id_alerta in {t.id for t in en_alerta}
        assert id_aprobada not in {t.id for t in en_alerta}
        assert all(t.estado is EstadoTransaccion.EN_ALERTA for t in en_alerta)

    # --- aprobar ---------------------------------------------------------------------

    def test_aprobar_en_alerta_con_partidas(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        t = repo.aprobar(id_)
        assert t.estado is EstadoTransaccion.APROBADA_CONTADOR
        assert repo.obtener(id_).estado is EstadoTransaccion.APROBADA_CONTADOR
        assert len(repo.obtener(id_).partidas) == 3

    def test_aprobar_sin_partidas_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        with pytest.raises(TransicionInvalidaError):
            repo.aprobar(id_)
        assert repo.obtener(id_).estado is EstadoTransaccion.EN_ALERTA

    def test_aprobar_aprobada_automatica_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        with pytest.raises(TransicionInvalidaError):
            repo.aprobar(id_)
        assert repo.obtener(id_).estado is EstadoTransaccion.APROBADA_AUTOMATICA

    def test_aprobar_dos_veces_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(id_)
        with pytest.raises(TransicionInvalidaError):
            repo.aprobar(id_)

    def test_aprobar_inexistente(self, repo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.aprobar(uuid.uuid4())

    # --- corregir --------------------------------------------------------------------

    def test_corregir_desde_en_alerta_reemplaza_partidas(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        nuevas = partidas_correctas(catalogo)
        t = repo.corregir(id_, nuevas)
        assert t.estado is EstadoTransaccion.CORREGIDA_CONTADOR
        guardada = repo.obtener(id_)
        assert guardada.estado is EstadoTransaccion.CORREGIDA_CONTADOR
        assert [(p.cuenta, p.naturaleza, p.valor, p.descripcion) for p in guardada.partidas] == [
            (p.cuenta, p.naturaleza, p.valor, p.descripcion) for p in nuevas
        ]

    def test_corregir_desde_aprobada_automatica(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        partidas = [
            Partida(catalogo.resolver("bancos"), Naturaleza.DEBITO, Decimal("119000")),
            Partida("4135", Naturaleza.CREDITO, Decimal("100000")),
            Partida(catalogo.cuenta_iva(), Naturaleza.CREDITO, Decimal("19000")),
        ]
        t = repo.corregir(id_, partidas)
        assert t.estado is EstadoTransaccion.CORREGIDA_CONTADOR
        assert catalogo.resolver("bancos") in {p.cuenta for p in repo.obtener(id_).partidas}

    def test_corregir_desde_aprobada_contador_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(id_)
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo))
        assert repo.obtener(id_).estado is EstadoTransaccion.APROBADA_CONTADOR

    def test_corregir_desde_corregida_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        repo.corregir(id_, partidas_correctas(catalogo))
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo))

    # --- asiento propuesto por el agente ---------------------------------------------

    def test_sin_correccion_no_hay_asiento_propuesto(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        t = repo.obtener(id_)
        assert t.asiento_propuesto is None
        assert t.corregido_en is None

    def test_corregir_conserva_el_asiento_propuesto_por_el_agente(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        propuesta = repo.obtener(id_).partidas
        assert len(propuesta) == 3

        repo.corregir(id_, _partidas_con_bancos(catalogo))

        t = repo.obtener(id_)
        assert t.asiento_propuesto == propuesta
        assert t.corregido_en is not None
        assert t.corregido_en.tzinfo is not None
        assert catalogo.resolver("bancos") in {p.cuenta for p in t.partidas}
        assert t.partidas != propuesta

    def test_corregir_sin_asiento_del_agente_guarda_propuesta_vacia(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        repo.corregir(id_, partidas_correctas(catalogo))
        t = repo.obtener(id_)
        assert t.asiento_propuesto == ()  # el agente no propuso partidas (distinto de None: sí hubo corrección)
        assert t.corregido_en is not None

    def test_segunda_correccion_no_sobrescribe_la_propuesta_original(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        propuesta = repo.obtener(id_).partidas
        repo.corregir(id_, _partidas_con_bancos(catalogo))
        primera = repo.obtener(id_)

        # La regla vigente no permite corregir dos veces; si se intenta, nada cambia.
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo))

        t = repo.obtener(id_)
        assert t.asiento_propuesto == propuesta
        assert t.corregido_en == primera.corregido_en
        assert t.partidas == primera.partidas

    def test_correccion_rechazada_no_guarda_propuesta(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        descuadrado = [
            Partida(catalogo.resolver("caja"), Naturaleza.DEBITO, Decimal("1")),
            Partida("4135", Naturaleza.CREDITO, Decimal("2")),
        ]
        with pytest.raises(AsientoInvalidoError):
            repo.corregir(id_, descuadrado)
        t = repo.obtener(id_)
        assert t.asiento_propuesto is None
        assert t.corregido_en is None


    def test_corregir_asiento_descuadrado_no_cambia_nada(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        antes = repo.obtener(id_)
        descuadrado = [
            Partida(catalogo.resolver("caja"), Naturaleza.DEBITO, Decimal("119000")),
            Partida("4135", Naturaleza.CREDITO, Decimal("100000")),
        ]
        with pytest.raises(AsientoInvalidoError):
            repo.corregir(id_, descuadrado)
        despues = repo.obtener(id_)
        assert despues.estado is EstadoTransaccion.EN_ALERTA
        assert despues.partidas == antes.partidas

    def test_corregir_cuenta_inexistente(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        partidas = [
            Partida("9999", Naturaleza.DEBITO, Decimal("100")),
            Partida("4135", Naturaleza.CREDITO, Decimal("100")),
        ]
        with pytest.raises(CuentaNoExisteError):
            repo.corregir(id_, partidas)
        assert repo.obtener(id_).estado is EstadoTransaccion.EN_ALERTA

    def test_corregir_inexistente(self, repo, catalogo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.corregir(uuid.uuid4(), partidas_correctas(catalogo))

    # --- factura sin número ------------------------------------------------------------

    def test_factura_sin_numero_se_guarda_en_alerta_con_motivo(self, repo, catalogo):
        id_ = repo.guardar(resultado_sin_numero(catalogo))
        t = repo.obtener(id_)
        assert re.fullmatch(r"SIN-NUMERO-[0-9a-f]{8}", t.numero_factura)
        assert t.estado is EstadoTransaccion.EN_ALERTA
        assert MOTIVO_SIN_NUMERO in t.motivos_alerta
        assert t in repo.listar(EstadoTransaccion.EN_ALERTA)

    def test_dos_facturas_sin_numero_no_se_consideran_duplicadas(self, repo, catalogo):
        primera = repo.obtener(repo.guardar(resultado_sin_numero(catalogo)))
        segunda = repo.obtener(repo.guardar(resultado_sin_numero(catalogo)))
        assert primera.numero_factura != segunda.numero_factura

    def test_factura_sin_numero_se_puede_aprobar_tras_revision(self, repo, catalogo):
        id_ = repo.guardar(resultado_sin_numero(catalogo))
        assert repo.aprobar(id_).estado is EstadoTransaccion.APROBADA_CONTADOR

    # --- cuenta del catálogo que no está sembrada en la base -----------------------------

    def test_guardar_con_cuenta_no_sembrada_lanza_error_y_no_guarda(self, repo, nuevo_numero, catalogo):
        numero = nuevo_numero()
        with pytest.raises(CuentaNoSembradaError) as error:
            repo.guardar(resultado_con_cuenta_no_sembrada(catalogo, numero))
        assert error.value.cuenta == CUENTA_NO_SEMBRADA
        assert str(error.value) == (
            f"La cuenta {CUENTA_NO_SEMBRADA} no está en la base de datos. Ejecute scripts/sembrar_puc.py."
        )
        assert all(t.numero_factura != numero for t in repo.listar())
        # Tras el rechazo, la misma factura se puede guardar sin problema.
        assert repo.guardar(resultado_aprobado(catalogo, numero))

    def test_corregir_con_cuenta_no_sembrada_lanza_error_y_no_cambia_nada(
        self, repo_extendido, nuevo_numero, catalogo
    ):
        id_ = repo_extendido.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        antes = repo_extendido.obtener(id_)

        with pytest.raises(CuentaNoSembradaError) as error:
            repo_extendido.corregir(id_, partidas_con_cuenta_no_sembrada(catalogo))
        assert error.value.cuenta == CUENTA_NO_SEMBRADA

        despues = repo_extendido.obtener(id_)
        assert despues.estado is EstadoTransaccion.EN_ALERTA
        assert despues.partidas == antes.partidas
        assert despues.asiento_propuesto is None
        assert despues.corregido_en is None
