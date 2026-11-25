"""Correo de las notificaciones (RF04): contrato de envíos en memoria, despachador y SMTP falso (aiosmtpd).

Nunca se envían correos reales: el SMTP es un servidor local de aiosmtpd en un puerto libre.
"""

import socket
from types import SimpleNamespace

import pytest
from aiosmtpd.controller import Controller
from contrato_notificaciones import ContratoEnviosCorreo, causa
from repositorio_notificaciones_memoria import RepositorioNotificacionesMemoria

from agente.config import Settings
from agente.domain.enums import Rol
from agente.domain.usuarios import Usuario
from agente.infrastructure.notificaciones.correo import EnviadorSMTP

USUARIOS = [
    Usuario(1, "contadora", "Contadora", Rol.CONTADOR, email="contador@empresa.co"),
    Usuario(2, "auxiliar", "Auxiliar", Rol.AUXILIAR),
    Usuario(3, "admin", "Admin", Rol.ADMIN, email="admin@empresa.co"),
]


@pytest.fixture
def notificaciones():
    return RepositorioNotificacionesMemoria(lambda: USUARIOS, correo_activo=True)


@pytest.fixture
def usuarios():
    return {"contador": 1, "auxiliar": 2, "admin": 3}


class TestEnviosCorreoMemoria(ContratoEnviosCorreo):
    pass


def test_sin_smtp_no_hay_envios():
    notificaciones = RepositorioNotificacionesMemoria(lambda: USUARIOS, correo_activo=False)
    notificaciones.registrar(causa())
    assert notificaciones.envios() == []
    assert notificaciones.listar(3)[0].correo is None


# --- Configuración --------------------------------------------------------------------------------------------


def test_el_correo_solo_se_activa_con_host_y_remitente(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # sin .env
    for nombre in ("SMTP_HOST", "SMTP_REMITENTE"):
        monkeypatch.delenv(nombre, raising=False)
    assert Settings().correo_activo is False
    monkeypatch.setenv("SMTP_HOST", "smtp.empresa.co")
    assert Settings().correo_activo is False
    monkeypatch.setenv("SMTP_REMITENTE", "agente@empresa.co")
    configuracion = Settings()
    assert configuracion.correo_activo is True
    assert (configuracion.smtp_puerto, configuracion.smtp_seguridad, configuracion.correo_intentos) == (
        587,
        "starttls",
        3,
    )


@pytest.mark.parametrize(
    ("variable", "valor"), [("SMTP_PUERTO", "correo"), ("SMTP_SEGURIDAD", "tls13"), ("CORREO_INTENTOS", "0")]
)
def test_configuracion_de_correo_invalida(monkeypatch, tmp_path, variable, valor):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(variable, valor)
    with pytest.raises(ValueError, match=variable):
        Settings()


# --- Envío SMTP con un servidor falso ---------------------------------------------------------------------


class Buzon:
    def __init__(self):
        self.recibidos = []

    async def handle_DATA(self, server, session, envelope):
        self.recibidos.append(envelope)
        return "250 OK"


def _puerto_libre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_falso():
    buzon = Buzon()
    controlador = Controller(buzon, hostname="127.0.0.1", port=_puerto_libre())
    controlador.start()
    try:
        yield buzon, controlador.port
    finally:
        controlador.stop()


def _configuracion(puerto):
    return SimpleNamespace(
        smtp_host="127.0.0.1",
        smtp_puerto=puerto,
        smtp_usuario="",
        smtp_contrasena="",
        smtp_remitente="agente@empresa.co",
        smtp_seguridad="ninguna",
    )


def test_el_enviador_smtp_entrega_el_mensaje(smtp_falso):
    buzon, puerto = smtp_falso
    EnviadorSMTP(_configuracion(puerto))("contador@empresa.co", "[Agente PUC] Transacción en alerta", "FV-1 en alerta")
    (sobre,) = buzon.recibidos
    assert sobre.mail_from == "agente@empresa.co" and sobre.rcpt_tos == ["contador@empresa.co"]
    contenido = sobre.content.decode("utf-8")
    assert "Subject: [Agente PUC] =?utf-8?" in contenido or "Transacci" in contenido
    assert "FV-1 en alerta" in contenido


def test_el_enviador_smtp_falla_si_no_hay_servidor():
    with pytest.raises(OSError):
        EnviadorSMTP(_configuracion(_puerto_libre()), timeout=2)("a@b.co", "asunto", "cuerpo")
