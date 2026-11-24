"""Documento original único por SHA-256

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


class DocumentosRepetidosError(RuntimeError):
    """Hay documentos con el mismo SHA-256: la restricción única no se puede crear."""


def documentos_repetidos(conexion) -> list[tuple[str, list[tuple[str, str]]]]:
    """[(sha256, [(id de la transacción, número de factura), ...]), ...] de los SHA-256 que se repiten."""
    filas = conexion.execute(
        sa.text(
            """
            SELECT d.sha256, d.transaccion_id::text, t.numero_factura
            FROM documento d
            JOIN transaccion t ON t.id = d.transaccion_id
            WHERE d.sha256 IN (SELECT sha256 FROM documento GROUP BY sha256 HAVING count(*) > 1)
            ORDER BY d.sha256, d.recibido_en, d.transaccion_id
            """
        )
    ).all()
    repetidos: dict[str, list[tuple[str, str]]] = {}
    for sha256, transaccion_id, numero in filas:
        repetidos.setdefault(sha256, []).append((transaccion_id, numero))
    return list(repetidos.items())


def mensaje_repetidos(repetidos: list[tuple[str, list[tuple[str, str]]]]) -> str:
    lineas = [
        "No se puede crear la restricción única uq_documento_sha256: hay documentos repetidos "
        f"({len(repetidos)} SHA-256). No se borró nada. Revise estas transacciones, decida cuál conservar "
        "y vuelva a ejecutar 'uv run alembic upgrade head':"
    ]
    for sha256, transacciones in repetidos:
        lineas.append(f"  SHA-256 {sha256}:")
        lineas.extend(f"    - transacción {id_} (factura {numero})" for id_, numero in transacciones)
    return "\n".join(lineas)


def upgrade() -> None:
    # El mismo archivo no se registra dos veces, aunque cambie el número de factura
    # (p. ej. una factura sin número recibe cada vez un número provisional distinto).
    # Antes de crear la restricción se buscan los repetidos que ya existan: si los hay, la migración
    # se detiene con la lista (nunca borra datos; la decisión de qué conservar es del contador).
    repetidos = documentos_repetidos(op.get_bind())
    if repetidos:
        raise DocumentosRepetidosError(mensaje_repetidos(repetidos))
    op.create_unique_constraint("uq_documento_sha256", "documento", ["sha256"])


def downgrade() -> None:
    op.drop_constraint("uq_documento_sha256", "documento", type_="unique")
