"""Envío de correo por SMTP (canal opcional de las notificaciones, RF04)."""

from __future__ import annotations

import smtplib
import ssl
from email.message import EmailMessage

from ...config import Settings


class EnviadorSMTP:
    """enviar(destinatario, asunto, cuerpo). Cualquier falla sube como excepción (el despachador reintenta)."""

    def __init__(self, settings: Settings, timeout: float = 15.0) -> None:
        self.host = settings.smtp_host
        self.puerto = settings.smtp_puerto
        self.usuario = settings.smtp_usuario
        self.contrasena = settings.smtp_contrasena
        self.remitente = settings.smtp_remitente
        self.seguridad = settings.smtp_seguridad
        self.timeout = timeout

    def __call__(self, destinatario: str, asunto: str, cuerpo: str) -> None:
        mensaje = EmailMessage()
        mensaje["From"] = self.remitente
        mensaje["To"] = destinatario
        mensaje["Subject"] = asunto
        mensaje.set_content(cuerpo)
        if self.seguridad == "ssl":
            conexion = smtplib.SMTP_SSL(
                self.host, self.puerto, timeout=self.timeout, context=ssl.create_default_context()
            )
        else:
            conexion = smtplib.SMTP(self.host, self.puerto, timeout=self.timeout)
        with conexion as smtp:
            if self.seguridad == "starttls":
                smtp.starttls(context=ssl.create_default_context())
            if self.usuario:
                smtp.login(self.usuario, self.contrasena)
            smtp.send_message(mensaje)
