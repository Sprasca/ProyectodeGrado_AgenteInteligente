"""Pruebas de contrato de RepositorioTransacciones.

Las heredan test_repositorio_memoria.py (unitarias) y test_repositorio_sql.py (integración);
cada módulo define los fixtures `repo` y `nuevo_numero` (genera números de factura únicos).
"""

import uuid
from decimal import Decimal

import pytest
from fabricas import (
    partidas_correctas,
    resultado_alerta_con_asiento,
    resultado_alerta_sin_asiento,
    resultado_aprobado,
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
        assert linea["palabras_encontradas"] == ["venta", "mercancia", "producto"]

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
