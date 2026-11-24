"""Pruebas de contrato de RepositorioTransacciones.

Las heredan test_repositorio_memoria.py (unitarias) y test_repositorio_sql.py (integración);
cada módulo define los fixtures `repo`, `id_usuario` (un usuario que exista),
`nuevo_numero` (genera números de factura únicos) y
`repo_extendido` (su catálogo tiene CUENTA_NO_SEMBRADA, que la base no tiene).
El `repo` de integración debe borrar al final todo lo que guardó, incluidas las facturas
con número provisional (SIN-NUMERO-...), que no usan `nuevo_numero`.
"""

import hashlib
import re
import uuid
from decimal import Decimal

import pytest
from fabricas import (
    CUENTA_NO_SEMBRADA,
    cufe_unico,
    partidas_con_cuenta_no_sembrada,
    partidas_correctas,
    resultado_alerta_con_asiento,
    resultado_alerta_sin_asiento,
    resultado_aprobado,
    resultado_con_cuenta_no_sembrada,
    resultado_con_cufe,
    resultado_sin_numero,
)

from agente.domain.asiento import Partida
from agente.domain.documento import DocumentoOriginal
from agente.domain.enums import CanalRecepcion, EstadoTransaccion, Naturaleza, TipoDocumento, TipoEvento
from agente.domain.errors import (
    AsientoInvalidoError,
    CuentaNoExisteError,
    FacturaDuplicadaError,
    MotivoInvalidoError,
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


# corregir exige un motivo de al menos 10 caracteres.
MOTIVO = "Corrección de prueba del contrato"


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
        t = repo.corregir(id_, nuevas, motivo=MOTIVO)
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
        t = repo.corregir(id_, partidas, motivo=MOTIVO)
        assert t.estado is EstadoTransaccion.CORREGIDA_CONTADOR
        assert catalogo.resolver("bancos") in {p.cuenta for p in repo.obtener(id_).partidas}

    def test_corregir_desde_aprobada_contador_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(id_)
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)
        assert repo.obtener(id_).estado is EstadoTransaccion.APROBADA_CONTADOR

    def test_corregir_desde_corregida_es_transicion_invalida(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)

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

        repo.corregir(id_, _partidas_con_bancos(catalogo), motivo=MOTIVO)

        t = repo.obtener(id_)
        assert t.asiento_propuesto == propuesta
        assert t.corregido_en is not None
        assert t.corregido_en.tzinfo is not None
        assert catalogo.resolver("bancos") in {p.cuenta for p in t.partidas}
        assert t.partidas != propuesta

    def test_corregir_sin_asiento_del_agente_guarda_propuesta_vacia(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)
        t = repo.obtener(id_)
        assert t.asiento_propuesto == ()  # el agente no propuso partidas (distinto de None: sí hubo corrección)
        assert t.corregido_en is not None

    def test_segunda_correccion_no_sobrescribe_la_propuesta_original(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        propuesta = repo.obtener(id_).partidas
        repo.corregir(id_, _partidas_con_bancos(catalogo), motivo=MOTIVO)
        primera = repo.obtener(id_)

        # La regla vigente no permite corregir dos veces; si se intenta, nada cambia.
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)

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
            repo.corregir(id_, descuadrado, motivo=MOTIVO)
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
            repo.corregir(id_, descuadrado, motivo=MOTIVO)
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
            repo.corregir(id_, partidas, motivo=MOTIVO)
        assert repo.obtener(id_).estado is EstadoTransaccion.EN_ALERTA

    def test_corregir_inexistente(self, repo, catalogo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.corregir(uuid.uuid4(), partidas_correctas(catalogo), motivo=MOTIVO)

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
            repo_extendido.corregir(id_, partidas_con_cuenta_no_sembrada(catalogo), motivo=MOTIVO)
        assert error.value.cuenta == CUENTA_NO_SEMBRADA

        despues = repo_extendido.obtener(id_)
        assert despues.estado is EstadoTransaccion.EN_ALERTA
        assert despues.partidas == antes.partidas
        assert despues.asiento_propuesto is None
        assert despues.corregido_en is None

    # --- trazabilidad: eventos con su usuario ------------------------------------------------

    def test_guardar_registra_recibida_y_clasificada(self, repo, nuevo_numero, catalogo, id_usuario):
        numero = nuevo_numero()
        id_ = repo.guardar(resultado_aprobado(catalogo, numero), usuario_id=id_usuario)
        recibida, clasificada = repo.eventos(id_)
        assert (recibida.tipo, recibida.usuario_id) == (TipoEvento.RECIBIDA, id_usuario)
        assert recibida.despues["numero_factura"] == numero
        assert (clasificada.tipo, clasificada.usuario_id) == (TipoEvento.CLASIFICADA, None)  # automático
        assert clasificada.despues["estado"] == "APROBADA_AUTOMATICA"
        assert len(clasificada.despues["partidas"]) == 3
        assert all(e.transaccion_id == id_ and e.fecha is not None for e in (recibida, clasificada))

    def test_aprobar_registra_evento_con_usuario_y_motivo(self, repo, nuevo_numero, catalogo, id_usuario):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(id_, usuario_id=id_usuario, motivo="  Revisado contra el soporte  ")
        evento = repo.eventos(id_)[-1]
        assert (evento.tipo, evento.usuario_id, evento.motivo) == (
            TipoEvento.APROBADA,
            id_usuario,
            "Revisado contra el soporte",
        )
        assert (evento.antes, evento.despues) == ({"estado": "EN_ALERTA"}, {"estado": "APROBADA_CONTADOR"})

    def test_aprobar_sin_motivo_deja_el_motivo_vacio(self, repo, nuevo_numero, catalogo, id_usuario):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(id_, usuario_id=id_usuario, motivo="   ")
        assert repo.eventos(id_)[-1].motivo is None

    def test_corregir_registra_evento_con_partidas_de_antes_y_despues(self, repo, nuevo_numero, catalogo, id_usuario):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        propuestas = repo.obtener(id_).partidas
        repo.corregir(id_, _partidas_con_bancos(catalogo), motivo=MOTIVO, usuario_id=id_usuario)
        evento = repo.eventos(id_)[-1]
        assert (evento.tipo, evento.usuario_id, evento.motivo) == (TipoEvento.CORREGIDA, id_usuario, MOTIVO)
        assert evento.antes["estado"] == "APROBADA_AUTOMATICA"
        assert [p["cuenta"] for p in evento.antes["partidas"]] == [p.cuenta for p in propuestas]
        assert evento.despues["estado"] == "CORREGIDA_CONTADOR"
        assert evento.despues["partidas"][0]["cuenta"] == catalogo.resolver("bancos")

    def test_eventos_de_transaccion_inexistente(self, repo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.eventos(uuid.uuid4())

    # --- rechazo y motivo obligatorio ----------------------------------------------------------

    @pytest.mark.parametrize(
        "fabrica", [resultado_alerta_con_asiento, resultado_alerta_sin_asiento, resultado_aprobado]
    )
    def test_rechazar_desde_en_alerta_o_aprobada_automatica(self, repo, nuevo_numero, catalogo, id_usuario, fabrica):
        id_ = repo.guardar(fabrica(catalogo, nuevo_numero()))
        estado_inicial = repo.obtener(id_).estado
        t = repo.rechazar(id_, motivo="  Factura emitida por error  ", usuario_id=id_usuario)
        assert t.estado is EstadoTransaccion.RECHAZADA
        assert repo.obtener(id_).estado is EstadoTransaccion.RECHAZADA
        evento = repo.eventos(id_)[-1]
        assert (evento.tipo, evento.usuario_id, evento.motivo) == (
            TipoEvento.RECHAZADA,
            id_usuario,
            "Factura emitida por error",
        )
        assert (evento.antes, evento.despues) == ({"estado": estado_inicial.value}, {"estado": "RECHAZADA"})

    def test_rechazada_es_estado_final(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.rechazar(id_, motivo=MOTIVO)
        with pytest.raises(TransicionInvalidaError):
            repo.aprobar(id_)
        with pytest.raises(TransicionInvalidaError):
            repo.corregir(id_, partidas_correctas(catalogo), motivo=MOTIVO)
        with pytest.raises(TransicionInvalidaError):
            repo.rechazar(id_, motivo=MOTIVO)
        assert repo.obtener(id_).estado is EstadoTransaccion.RECHAZADA
        # RECIBIDA, CLASIFICADA y RECHAZADA: los intentos rechazados no dejan evento.
        assert [e.tipo for e in repo.eventos(id_)] == [
            TipoEvento.RECIBIDA,
            TipoEvento.CLASIFICADA,
            TipoEvento.RECHAZADA,
        ]

    def test_no_se_rechaza_una_transaccion_ya_resuelta(self, repo, nuevo_numero, catalogo):
        aprobada = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        repo.aprobar(aprobada)
        corregida = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()))
        repo.corregir(corregida, partidas_correctas(catalogo), motivo=MOTIVO)
        for id_ in (aprobada, corregida):
            with pytest.raises(TransicionInvalidaError):
                repo.rechazar(id_, motivo=MOTIVO)

    def test_rechazar_inexistente(self, repo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.rechazar(uuid.uuid4(), motivo=MOTIVO)

    @pytest.mark.parametrize("motivo", ["", "corto", "   123456789   ", None])
    def test_corregir_y_rechazar_exigen_motivo_de_10_caracteres(self, repo, nuevo_numero, catalogo, motivo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        with pytest.raises(MotivoInvalidoError):
            repo.corregir(id_, partidas_correctas(catalogo), motivo=motivo)
        with pytest.raises(MotivoInvalidoError):
            repo.rechazar(id_, motivo=motivo)
        assert repo.obtener(id_).estado is EstadoTransaccion.EN_ALERTA
        assert len(repo.eventos(id_)) == 2

    def test_motivo_de_exactamente_10_caracteres_es_valido(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_alerta_con_asiento(catalogo, nuevo_numero()))
        assert repo.rechazar(id_, motivo="1234567890").estado is EstadoTransaccion.RECHAZADA

    # --- documento original y CUFE -----------------------------------------------------------

    def test_guardar_con_documento_original(self, repo, nuevo_numero, catalogo):
        contenido = "<Invoice>original con tildes: Ñandú</Invoice>".encode()
        documento = DocumentoOriginal.recibido("C:/carpeta/factura.xml", TipoDocumento.XML, contenido)
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()), documento=documento)

        guardado = repo.documento(id_)
        assert guardado.contenido == contenido
        assert guardado.nombre_archivo == "factura.xml"
        assert guardado.tipo is TipoDocumento.XML
        assert guardado.sha256 == hashlib.sha256(contenido).hexdigest()
        assert guardado.recibido_en is not None
        recibida = repo.eventos(id_)[0]
        assert recibida.despues["documento"] == {
            "nombre_archivo": "factura.xml",
            "tipo": "XML",
            "bytes": len(contenido),
            "sha256": hashlib.sha256(contenido).hexdigest(),
        }

    def test_sin_documento_devuelve_none(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        assert repo.documento(id_) is None

    def test_documento_de_transaccion_inexistente(self, repo):
        with pytest.raises(TransaccionNoEncontradaError):
            repo.documento(uuid.uuid4())

    def test_cufe_repetido_es_duplicado_aunque_cambie_el_numero(self, repo, nuevo_numero, catalogo):
        cufe = cufe_unico()
        id_ = repo.guardar(resultado_con_cufe(catalogo, nuevo_numero(), cufe))
        assert repo.obtener(id_).cufe == cufe
        assert repo.eventos(id_)[0].despues["cufe"] == cufe
        otro_numero = nuevo_numero()
        with pytest.raises(FacturaDuplicadaError, match=cufe):
            repo.guardar(resultado_con_cufe(catalogo, otro_numero, cufe))
        assert all(t.numero_factura != otro_numero for t in repo.listar())

    def test_varias_facturas_sin_cufe_no_chocan(self, repo, nuevo_numero, catalogo):
        primera = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        segunda = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        assert repo.obtener(primera).cufe is None and repo.obtener(segunda).cufe is None

    # --- canal de llegada ----------------------------------------------------------------------

    def test_recepcion_automatica_registra_canal_carpeta_y_usuario_nulo(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()), canal=CanalRecepcion.CARPETA)
        recibida = repo.eventos(id_)[0]
        assert (recibida.tipo, recibida.usuario_id) == (TipoEvento.RECIBIDA, None)
        assert recibida.despues["canal"] == "carpeta"

    def test_carga_por_api_registra_canal_api(self, repo, nuevo_numero, catalogo, id_usuario):
        resultado = resultado_aprobado(catalogo, nuevo_numero())
        id_ = repo.guardar(resultado, usuario_id=id_usuario, canal=CanalRecepcion.API)
        recibida = repo.eventos(id_)[0]
        assert (recibida.usuario_id, recibida.despues["canal"]) == (id_usuario, "api")

    def test_sin_canal_queda_nulo(self, repo, nuevo_numero, catalogo):
        id_ = repo.guardar(resultado_aprobado(catalogo, nuevo_numero()))
        assert repo.eventos(id_)[0].despues["canal"] is None

    # --- documento duplicado por SHA-256 ------------------------------------------------------------

    def test_mismo_documento_con_otro_numero_es_duplicado(self, repo, nuevo_numero, catalogo):
        contenido = b"<Invoice>mismo archivo, numero provisional distinto</Invoice>"
        primero = DocumentoOriginal.recibido("a.xml", TipoDocumento.XML, contenido)
        id_ = repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()), documento=primero)
        otro_numero = nuevo_numero()
        repetido = DocumentoOriginal.recibido("otro-nombre.xml", TipoDocumento.XML, contenido)
        with pytest.raises(FacturaDuplicadaError, match=f"SHA-256 {primero.sha256}") as error:
            repo.guardar(resultado_alerta_sin_asiento(catalogo, otro_numero), documento=repetido)
        assert str(id_) in str(error.value)
        assert all(t.numero_factura != otro_numero for t in repo.listar())

    def test_documento_registrado_por_sha256_con_nombre_y_canal(self, repo, nuevo_numero, catalogo):
        contenido = f"<Invoice>{uuid.uuid4()}</Invoice>".encode()
        documento = DocumentoOriginal.recibido("entrada/llego.xml", TipoDocumento.XML, contenido)
        id_ = repo.guardar(
            resultado_aprobado(catalogo, nuevo_numero()), documento=documento, canal=CanalRecepcion.CARPETA
        )
        registrado = repo.documento_registrado(documento.sha256)
        assert (registrado.transaccion_id, registrado.nombre_archivo, registrado.canal) == (
            id_,
            "llego.xml",
            CanalRecepcion.CARPETA,
        )

    def test_documento_registrado_por_api_y_sin_canal(self, repo, nuevo_numero, catalogo, id_usuario):
        por_api = DocumentoOriginal.recibido("api.xml", TipoDocumento.XML, f"<a>{uuid.uuid4()}</a>".encode())
        sin_canal = DocumentoOriginal.recibido("viejo.xml", TipoDocumento.XML, f"<b>{uuid.uuid4()}</b>".encode())
        resultado = resultado_aprobado(catalogo, nuevo_numero())
        repo.guardar(resultado, usuario_id=id_usuario, documento=por_api, canal=CanalRecepcion.API)
        repo.guardar(resultado_aprobado(catalogo, nuevo_numero()), documento=sin_canal)
        assert repo.documento_registrado(por_api.sha256).canal is CanalRecepcion.API
        assert repo.documento_registrado(sin_canal.sha256).canal is None

    def test_documento_no_registrado(self, repo):
        assert repo.documento_registrado(hashlib.sha256(uuid.uuid4().bytes).hexdigest()) is None

    def test_documentos_distintos_no_chocan(self, repo, nuevo_numero, catalogo):
        for contenido in (b"<Invoice>uno</Invoice>", b"<Invoice>dos</Invoice>"):
            documento = DocumentoOriginal.recibido("f.xml", TipoDocumento.XML, contenido)
            repo.guardar(resultado_alerta_sin_asiento(catalogo, nuevo_numero()), documento=documento)

