"""Pruebas de contrato de RepositorioCatalogo.

Las heredan test_repositorio_catalogo_memoria.py (unitarias) y test_repositorio_catalogo_sql.py
(integración). Cada módulo define los fixtures:
- repo_catalogo: repositorio con el catálogo de data/puc ya cargado (cuentas 1105, 4135, 4145...);
- cambios: lista que crece cada vez que el repositorio llama a al_cambiar;
- nuevo_codigo(): código de regla único; nueva_cuenta(): código de cuenta único (solo dígitos);
- palabra(): palabra clave única, para no chocar con reglas que ya existan en la base.
"""

import pytest

from agente.domain.enums import AccionHistorial, Naturaleza
from agente.domain.errors import (
    ConflictoReglaError,
    CuentaDuplicadaError,
    CuentaInvalidaError,
    ReglaInvalidaError,
    ReglaNoEncontradaError,
)
from agente.domain.puc import ReglaIngreso
from agente.domain.reglas import ConflictoPalabra


class ContratoCatalogo:
    # --- cuentas ------------------------------------------------------------------------

    def test_listar_cuentas_trae_el_catalogo_ordenado(self, repo_catalogo):
        cuentas = repo_catalogo.listar_cuentas()
        codigos = [c.codigo for c in cuentas]
        assert {"1105", "4135", "4145"} <= set(codigos)
        assert codigos == sorted(codigos)

    def test_crear_cuenta(self, repo_catalogo, nueva_cuenta, cambios):
        codigo = nueva_cuenta()
        cuenta = repo_catalogo.crear_cuenta(codigo, "  Ingresos de prueba  ", Naturaleza.CREDITO)
        assert (cuenta.codigo, cuenta.nombre, cuenta.naturaleza) == (codigo, "Ingresos de prueba", Naturaleza.CREDITO)
        assert cuenta in repo_catalogo.listar_cuentas()
        assert len(cambios) == 1

    def test_crear_cuenta_con_codigo_repetido(self, repo_catalogo, cambios):
        with pytest.raises(CuentaDuplicadaError, match="4135"):
            repo_catalogo.crear_cuenta("4135", "Otra", Naturaleza.CREDITO)
        nombres = {c.codigo: c.nombre for c in repo_catalogo.listar_cuentas()}
        assert nombres["4135"] != "Otra"
        assert cambios == []

    @pytest.mark.parametrize(("codigo", "nombre"), [("41A5", "Con letras"), ("4199", "  ")])
    def test_crear_cuenta_invalida(self, repo_catalogo, cambios, codigo, nombre):
        with pytest.raises(CuentaInvalidaError):
            repo_catalogo.crear_cuenta(codigo, nombre, Naturaleza.CREDITO)
        assert cambios == []

    def test_una_regla_puede_usar_una_cuenta_recien_creada(self, repo_catalogo, nueva_cuenta, nuevo_codigo, palabra):
        cuenta = nueva_cuenta()
        repo_catalogo.crear_cuenta(cuenta, "Ingresos de prueba", Naturaleza.CREDITO)
        guardada = repo_catalogo.crear_regla(ReglaIngreso(nuevo_codigo(), cuenta, (palabra(),)))
        assert guardada.regla.cuenta_ingreso == cuenta

    # --- crear regla ----------------------------------------------------------------------

    def test_crear_regla_normaliza_y_registra_historial(self, repo_catalogo, nuevo_codigo, palabra, cambios):
        codigo, clave = nuevo_codigo(), palabra()
        guardada = repo_catalogo.crear_regla(
            ReglaIngreso(f"  {codigo} ", "4135", (f" {clave.upper()} ", clave, "Ñandú"), "  Prueba  "),
            usuario="contadora",
        )
        regla = guardada.regla
        assert guardada.advertencias == ()
        assert (regla.codigo, regla.cuenta_ingreso, regla.descripcion) == (codigo, "4135", "Prueba")
        assert regla.palabras_clave == (clave, "nandu")
        assert regla.activa is True
        assert regla.creado_en is not None and regla.actualizado_en is not None
        assert repo_catalogo.obtener_regla(codigo) == regla
        assert len(cambios) == 1

        (entrada,) = repo_catalogo.historial(codigo)
        assert entrada.accion is AccionHistorial.CREAR
        assert entrada.antes is None
        assert entrada.despues == {
            "codigo": codigo,
            "cuenta_ingreso": "4135",
            "palabras_clave": [clave, "nandu"],
            "descripcion": "Prueba",
            "activa": True,
        }
        assert entrada.usuario == "contadora"
        assert entrada.fecha is not None

    @pytest.mark.parametrize(
        ("cuenta", "palabras", "fragmento"),
        [
            ("1105", ("x",), "clase 4 con naturaleza CREDITO"),
            ("999999999", ("x",), "no existe"),
            ("4135", (), "al menos una palabra clave"),
        ],
    )
    def test_crear_regla_con_errores_no_guarda(self, repo_catalogo, nuevo_codigo, cambios, cuenta, palabras, fragmento):
        codigo = nuevo_codigo()
        with pytest.raises(ReglaInvalidaError) as error:
            repo_catalogo.crear_regla(ReglaIngreso(codigo, cuenta, palabras))
        assert any(fragmento in e for e in error.value.errores)
        assert repo_catalogo.obtener_regla(codigo) is None
        assert cambios == []

    def test_crear_regla_con_codigo_repetido(self, repo_catalogo, nuevo_codigo, palabra):
        codigo = nuevo_codigo()
        repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (palabra(),)))
        with pytest.raises(ReglaInvalidaError, match="Ya existe una regla"):
            repo_catalogo.crear_regla(ReglaIngreso(codigo, "4145", (palabra(),)))
        assert repo_catalogo.obtener_regla(codigo).cuenta_ingreso == "4135"
        assert len(repo_catalogo.historial(codigo)) == 1

    def test_conflicto_sin_confirmar_no_guarda(self, repo_catalogo, nuevo_codigo, palabra, cambios):
        primera, segunda, clave = nuevo_codigo(), nuevo_codigo(), palabra()
        repo_catalogo.crear_regla(ReglaIngreso(primera, "4135", (clave,)))
        with pytest.raises(ConflictoReglaError) as conflicto:
            repo_catalogo.crear_regla(ReglaIngreso(segunda, "4145", (clave, palabra())))
        assert conflicto.value.advertencias == (ConflictoPalabra(clave, primera, "4135"),)
        assert repo_catalogo.obtener_regla(segunda) is None
        assert len(cambios) == 1  # solo la primera regla

    def test_conflicto_confirmado_guarda_y_queda_en_el_historial(self, repo_catalogo, nuevo_codigo, palabra):
        primera, segunda, clave = nuevo_codigo(), nuevo_codigo(), palabra()
        repo_catalogo.crear_regla(ReglaIngreso(primera, "4135", (clave,)))
        guardada = repo_catalogo.crear_regla(ReglaIngreso(segunda, "4145", (clave,)), confirmar=True)
        assert guardada.advertencias == (ConflictoPalabra(clave, primera, "4135"),)
        assert repo_catalogo.obtener_regla(segunda) is not None
        (entrada,) = repo_catalogo.historial(segunda)
        assert entrada.despues["advertencias_confirmadas"] == [{"palabra": clave, "regla": primera, "cuenta": "4135"}]

    def test_sin_conflicto_con_reglas_inactivas_ni_con_la_misma_cuenta(self, repo_catalogo, nuevo_codigo, palabra):
        inactiva, misma_cuenta, clave = nuevo_codigo(), nuevo_codigo(), palabra()
        repo_catalogo.crear_regla(ReglaIngreso(inactiva, "4135", (clave,)))
        repo_catalogo.desactivar_regla(inactiva)
        assert repo_catalogo.crear_regla(ReglaIngreso(nuevo_codigo(), "4145", (clave,))).advertencias == ()
        assert repo_catalogo.crear_regla(ReglaIngreso(misma_cuenta, "4145", (clave,))).advertencias == ()

    # --- editar regla ---------------------------------------------------------------------

    def test_editar_regla(self, repo_catalogo, nuevo_codigo, palabra, cambios):
        codigo, vieja, nueva = nuevo_codigo(), palabra(), palabra()
        creada = repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (vieja,), "Antes")).regla
        guardada = repo_catalogo.editar_regla(codigo, "4145", [nueva.upper(), nueva], "  Después ", usuario="contador")
        regla = guardada.regla
        assert (regla.codigo, regla.cuenta_ingreso, regla.palabras_clave, regla.descripcion) == (
            codigo,
            "4145",
            (nueva,),
            "Después",
        )
        assert regla.activa is True
        assert regla.creado_en == creada.creado_en
        assert regla.actualizado_en >= creada.actualizado_en
        assert repo_catalogo.obtener_regla(codigo) == regla
        assert len(cambios) == 2

        crear, editar = repo_catalogo.historial(codigo)
        assert crear.accion is AccionHistorial.CREAR
        assert editar.accion is AccionHistorial.EDITAR
        assert editar.antes["palabras_clave"] == [vieja] and editar.antes["cuenta_ingreso"] == "4135"
        assert editar.despues["palabras_clave"] == [nueva] and editar.despues["cuenta_ingreso"] == "4145"
        assert editar.usuario == "contador"

    def test_editar_regla_inexistente(self, repo_catalogo, nuevo_codigo):
        with pytest.raises(ReglaNoEncontradaError):
            repo_catalogo.editar_regla(nuevo_codigo(), "4135", ["x"])

    def test_editar_con_errores_no_cambia_nada(self, repo_catalogo, nuevo_codigo, palabra):
        codigo = nuevo_codigo()
        antes = repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (palabra(),))).regla
        with pytest.raises(ReglaInvalidaError):
            repo_catalogo.editar_regla(codigo, "1105", [])
        assert repo_catalogo.obtener_regla(codigo) == antes
        assert len(repo_catalogo.historial(codigo)) == 1

    def test_editar_con_conflicto_sin_confirmar_no_cambia_nada(self, repo_catalogo, nuevo_codigo, palabra):
        otra, codigo, clave = nuevo_codigo(), nuevo_codigo(), palabra()
        repo_catalogo.crear_regla(ReglaIngreso(otra, "4145", (clave,)))
        antes = repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (palabra(),))).regla
        with pytest.raises(ConflictoReglaError):
            repo_catalogo.editar_regla(codigo, "4135", [clave])
        assert repo_catalogo.obtener_regla(codigo) == antes

        guardada = repo_catalogo.editar_regla(codigo, "4135", [clave], confirmar=True)
        assert guardada.advertencias == (ConflictoPalabra(clave, otra, "4145"),)
        assert repo_catalogo.historial(codigo)[-1].despues["advertencias_confirmadas"]

    def test_editar_sin_cambiar_palabras_no_choca_consigo_misma(self, repo_catalogo, nuevo_codigo, palabra):
        codigo, clave = nuevo_codigo(), palabra()
        repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (clave,)))
        assert repo_catalogo.editar_regla(codigo, "4135", [clave], "Nueva descripción").advertencias == ()

    # --- activar / desactivar ---------------------------------------------------------------

    def test_desactivar_y_activar(self, repo_catalogo, nuevo_codigo, palabra, cambios):
        codigo = nuevo_codigo()
        repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (palabra(),)))

        assert repo_catalogo.desactivar_regla(codigo, usuario="contador").activa is False
        assert repo_catalogo.obtener_regla(codigo).activa is False
        assert codigo in {r.codigo for r in repo_catalogo.listar_reglas(activa=False)}
        assert codigo not in {r.codigo for r in repo_catalogo.listar_reglas(activa=True)}

        assert repo_catalogo.activar_regla(codigo).activa is True
        assert codigo in {r.codigo for r in repo_catalogo.listar_reglas(activa=True)}

        acciones = [e.accion for e in repo_catalogo.historial(codigo)]
        assert acciones == [AccionHistorial.CREAR, AccionHistorial.DESACTIVAR, AccionHistorial.ACTIVAR]
        desactivar = repo_catalogo.historial(codigo)[1]
        assert (desactivar.antes["activa"], desactivar.despues["activa"]) == (True, False)
        assert desactivar.usuario == "contador"
        assert len(cambios) == 3

    def test_repetir_el_mismo_estado_no_cambia_ni_deja_historial(self, repo_catalogo, nuevo_codigo, palabra, cambios):
        codigo = nuevo_codigo()
        repo_catalogo.crear_regla(ReglaIngreso(codigo, "4135", (palabra(),)))
        assert repo_catalogo.activar_regla(codigo).activa is True
        repo_catalogo.desactivar_regla(codigo)
        assert repo_catalogo.desactivar_regla(codigo).activa is False
        assert [e.accion for e in repo_catalogo.historial(codigo)] == [
            AccionHistorial.CREAR,
            AccionHistorial.DESACTIVAR,
        ]
        assert len(cambios) == 2

    @pytest.mark.parametrize("accion", ["activar_regla", "desactivar_regla", "historial"])
    def test_regla_inexistente(self, repo_catalogo, nuevo_codigo, accion):
        with pytest.raises(ReglaNoEncontradaError):
            getattr(repo_catalogo, accion)(nuevo_codigo())

    def test_obtener_regla_inexistente(self, repo_catalogo, nuevo_codigo):
        assert repo_catalogo.obtener_regla(nuevo_codigo()) is None

    # --- listar ---------------------------------------------------------------------------

    def test_listar_reglas_ordenadas_y_filtradas(self, repo_catalogo, nuevo_codigo, palabra):
        activa, inactiva = nuevo_codigo(), nuevo_codigo()
        repo_catalogo.crear_regla(ReglaIngreso(activa, "4135", (palabra(),)))
        repo_catalogo.crear_regla(ReglaIngreso(inactiva, "4135", (palabra(),)))
        repo_catalogo.desactivar_regla(inactiva)

        todas = repo_catalogo.listar_reglas()
        codigos = [r.codigo for r in todas]
        assert codigos == sorted(codigos)
        assert {activa, inactiva, "R-4135-COMERCIO"} <= set(codigos)
        assert all(r.activa for r in repo_catalogo.listar_reglas(activa=True))
        assert all(not r.activa for r in repo_catalogo.listar_reglas(activa=False))
