"""scripts/crear_usuario.py: pide la contraseña con getpass (sin mostrarla) y crea el usuario."""

import importlib.util
from pathlib import Path

import pytest
from repositorio_usuarios_memoria import RepositorioUsuariosMemoria

from agente.domain.enums import Rol

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "crear_usuario.py"


@pytest.fixture
def script():
    spec = importlib.util.spec_from_file_location("crear_usuario", SCRIPT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def _lector(*respuestas):
    preguntas: list[str] = []
    pendientes = list(respuestas)

    def leer(pregunta):
        preguntas.append(pregunta)
        return pendientes.pop(0)

    return leer, preguntas


def test_usa_getpass_por_defecto(script):
    import getpass
    import inspect

    assert inspect.signature(script.main).parameters["leer_contrasena"].default is getpass.getpass


def test_crea_el_usuario_con_la_contrasena_confirmada(script, capsys):
    repo = RepositorioUsuariosMemoria()
    leer, preguntas = _lector("clave-segura-1", "clave-segura-1")
    usuario = script.main(["Ana.Admin", "--nombre-completo", "Ana Admin", "--rol", "admin"], leer, repo)
    assert (usuario.nombre_usuario, usuario.rol) == ("ana.admin", Rol.ADMIN)
    assert preguntas == ["Contraseña: ", "Repita la contraseña: "]
    assert repo.autenticar("ana.admin", "clave-segura-1") is not None
    salida = capsys.readouterr().out
    assert "Usuario creado: ana.admin" in salida
    assert "clave-segura-1" not in salida


def test_contrasenas_distintas_no_crean_nada(script):
    repo = RepositorioUsuariosMemoria()
    leer, _ = _lector("clave-segura-1", "otra-clave-1")
    with pytest.raises(SystemExit, match="no coinciden"):
        script.main(["ana", "--nombre-completo", "Ana", "--rol", "ADMIN"], leer, repo)
    assert repo.listar() == []


def test_datos_invalidos_se_informan(script):
    repo = RepositorioUsuariosMemoria()
    leer, _ = _lector("corta", "corta")
    with pytest.raises(SystemExit, match="al menos 8"):
        script.main(["ana", "--nombre-completo", "Ana", "--rol", "ADMIN"], leer, repo)


def test_usuario_repetido(script):
    repo = RepositorioUsuariosMemoria()
    repo.crear("ana", "Ana", "clave-segura-1", Rol.ADMIN)
    leer, _ = _lector("clave-segura-1", "clave-segura-1")
    with pytest.raises(SystemExit, match="Ya existe"):
        script.main(["ana", "--nombre-completo", "Ana", "--rol", "ADMIN"], leer, repo)


def test_rol_desconocido(script):
    with pytest.raises(SystemExit):
        script.main(["ana", "--nombre-completo", "Ana", "--rol", "JEFE"], _lector()[0], RepositorioUsuariosMemoria())
