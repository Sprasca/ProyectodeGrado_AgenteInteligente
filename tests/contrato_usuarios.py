"""Pruebas de contrato de RepositorioUsuarios (memoria: unitarias; SQL: integración).

Cada módulo define `repo_usuarios` y `nombre()` (un nombre de usuario único que se limpia al final).
"""

import pytest

from agente.domain.enums import Rol
from agente.domain.errors import UsuarioDuplicadoError, UsuarioInvalidoError, UsuarioNoEncontradoError
from agente.domain.usuarios import Usuario

CLAVE = "clave-segura-123"


class ContratoUsuarios:
    def test_crear_y_autenticar(self, repo_usuarios, nombre):
        n = nombre()
        creado = repo_usuarios.crear(f"  {n.upper()} ", "  Ana Pérez  ", CLAVE, Rol.CONTADOR)
        assert isinstance(creado, Usuario)
        assert (creado.nombre_usuario, creado.nombre_completo, creado.rol, creado.activo) == (
            n,
            "Ana Pérez",
            Rol.CONTADOR,
            True,
        )
        assert creado.creado_en is not None
        assert not hasattr(creado, "hash_contrasena")
        assert repo_usuarios.autenticar(n.upper(), CLAVE) == creado
        assert repo_usuarios.obtener(creado.id) == creado
        assert creado in repo_usuarios.listar()

    @pytest.mark.parametrize("contrasena", ["otra-clave-123", "", CLAVE.upper()])
    def test_contrasena_incorrecta(self, repo_usuarios, nombre, contrasena):
        n = nombre()
        repo_usuarios.crear(n, "Ana", CLAVE, Rol.AUXILIAR)
        assert repo_usuarios.autenticar(n, contrasena) is None

    def test_usuario_inexistente(self, repo_usuarios, nombre):
        assert repo_usuarios.autenticar(nombre(), CLAVE) is None
        assert repo_usuarios.obtener(987654321) is None

    def test_usuario_inactivo_no_se_autentica(self, repo_usuarios, nombre):
        n = nombre()
        creado = repo_usuarios.crear(n, "Ana", CLAVE, Rol.AUXILIAR)
        repo_usuarios.actualizar(creado.id, activo=False)
        assert repo_usuarios.autenticar(n, CLAVE) is None
        assert repo_usuarios.obtener(creado.id).activo is False

    def test_nombre_repetido(self, repo_usuarios, nombre):
        n = nombre()
        repo_usuarios.crear(n, "Ana", CLAVE, Rol.AUXILIAR)
        with pytest.raises(UsuarioDuplicadoError):
            repo_usuarios.crear(n.upper(), "Otra Ana", CLAVE, Rol.CONTADOR)

    @pytest.mark.parametrize(
        ("usuario", "completo", "contrasena"),
        [("ab", "Ana", CLAVE), ("con espacio", "Ana", CLAVE), ("valido", " ", CLAVE), ("valido", "Ana", "corta")],
    )
    def test_datos_invalidos(self, repo_usuarios, usuario, completo, contrasena):
        with pytest.raises(UsuarioInvalidoError):
            repo_usuarios.crear(usuario, completo, contrasena, Rol.AUXILIAR)

    def test_actualizar_rol_estado_y_nombre(self, repo_usuarios, nombre):
        creado = repo_usuarios.crear(nombre(), "Ana", CLAVE, Rol.AUXILIAR)
        cambiado = repo_usuarios.actualizar(creado.id, rol=Rol.CONTADOR, nombre_completo="  Ana María  ")
        assert (cambiado.rol, cambiado.nombre_completo, cambiado.activo) == (Rol.CONTADOR, "Ana María", True)
        assert repo_usuarios.obtener(creado.id) == cambiado
        assert repo_usuarios.actualizar(creado.id, activo=False).activo is False
        assert repo_usuarios.actualizar(creado.id, activo=True).activo is True

    def test_actualizar_inexistente(self, repo_usuarios):
        with pytest.raises(UsuarioNoEncontradoError):
            repo_usuarios.actualizar(987654321, activo=False)

    def test_nombre_completo_vacio_al_actualizar(self, repo_usuarios, nombre):
        creado = repo_usuarios.crear(nombre(), "Ana", CLAVE, Rol.AUXILIAR)
        with pytest.raises(UsuarioInvalidoError):
            repo_usuarios.actualizar(creado.id, nombre_completo="   ")

    def test_admin_se_puede_desactivar_si_hay_otro_admin_activo(self, repo_usuarios, nombre):
        primero = repo_usuarios.crear(nombre(), "Admin 1", CLAVE, Rol.ADMIN)
        repo_usuarios.crear(nombre(), "Admin 2", CLAVE, Rol.ADMIN)
        assert repo_usuarios.actualizar(primero.id, activo=False).activo is False

    def test_correo_opcional_se_normaliza_cambia_y_se_quita(self, repo_usuarios, nombre):
        sin = repo_usuarios.crear(nombre(), "Sin correo", "clave-segura-1", Rol.AUXILIAR)
        assert sin.email is None
        con = repo_usuarios.crear(nombre(), "Con correo", "clave-segura-1", Rol.CONTADOR, " A@B.Co ")
        assert con.email == "a@b.co" and repo_usuarios.obtener(con.id).email == "a@b.co"
        assert repo_usuarios.actualizar(con.id, email="nuevo@b.co").email == "nuevo@b.co"
        assert repo_usuarios.actualizar(con.id, activo=True).email == "nuevo@b.co"  # sin email no lo toca
        assert repo_usuarios.actualizar(con.id, email="").email is None

    @pytest.mark.parametrize("email", ["sin-arroba", "a@b", "a b@c.co"])
    def test_correo_invalido(self, repo_usuarios, nombre, email):
        with pytest.raises(UsuarioInvalidoError, match="no es una dirección válida"):
            repo_usuarios.crear(nombre(), "Mal correo", "clave-segura-1", Rol.AUXILIAR, email)

