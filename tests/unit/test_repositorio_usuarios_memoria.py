import itertools

import pytest
from contrato_usuarios import CLAVE, ContratoUsuarios
from repositorio_usuarios_memoria import RepositorioUsuariosMemoria

from agente.domain.enums import Rol
from agente.domain.errors import UltimoAdministradorError
from agente.domain.usuarios import Usuario, validar_cambio_de_administrador


@pytest.fixture
def repo_usuarios():
    return RepositorioUsuariosMemoria()


@pytest.fixture
def nombre():
    contador = itertools.count(1)
    return lambda: f"usuario{next(contador)}"


class TestRepositorioUsuariosMemoria(ContratoUsuarios):
    pass


# El último ADMIN activo solo se puede probar en una base aislada: en PostgreSQL puede haber otros ADMIN.


@pytest.mark.parametrize("cambio", [{"activo": False}, {"rol": Rol.CONTADOR}, {"rol": Rol.AUXILIAR, "activo": False}])
def test_no_se_puede_quitar_el_ultimo_admin_activo(repo_usuarios, cambio):
    admin = repo_usuarios.crear("unico-admin", "Admin", CLAVE, Rol.ADMIN)
    with pytest.raises(UltimoAdministradorError):
        repo_usuarios.actualizar(admin.id, **cambio)
    assert repo_usuarios.obtener(admin.id) == admin


def test_un_admin_inactivo_no_cuenta_como_respaldo(repo_usuarios):
    activo = repo_usuarios.crear("admin-activo", "Admin", CLAVE, Rol.ADMIN)
    inactivo = repo_usuarios.crear("admin-inactivo", "Admin", CLAVE, Rol.ADMIN)
    repo_usuarios.actualizar(inactivo.id, activo=False)
    with pytest.raises(UltimoAdministradorError):
        repo_usuarios.actualizar(activo.id, activo=False)


def test_cambios_que_no_afectan_al_ultimo_admin_si_se_permiten(repo_usuarios):
    admin = repo_usuarios.crear("unico-admin", "Admin", CLAVE, Rol.ADMIN)
    assert repo_usuarios.actualizar(admin.id, nombre_completo="Nuevo nombre").rol is Rol.ADMIN
    assert repo_usuarios.actualizar(admin.id, rol=Rol.ADMIN, activo=True).activo is True


def test_regla_del_ultimo_admin_en_el_dominio():
    admin = Usuario(1, "admin", "Admin", Rol.ADMIN)
    with pytest.raises(UltimoAdministradorError):
        validar_cambio_de_administrador(admin, Rol.CONTADOR, True, otros_admins_activos=0)
    validar_cambio_de_administrador(admin, Rol.CONTADOR, True, otros_admins_activos=1)
    # Un ADMIN ya inactivo, o alguien que no es ADMIN, no activa la regla.
    validar_cambio_de_administrador(Usuario(2, "x", "X", Rol.ADMIN, activo=False), Rol.CONTADOR, False, 0)
    validar_cambio_de_administrador(Usuario(3, "y", "Y", Rol.CONTADOR), Rol.AUXILIAR, False, 0)
