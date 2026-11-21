"""Carga el catálogo semilla (data/puc/*.csv) en PostgreSQL.

Sin opciones solo INSERTA las cuentas y reglas que no existan: no sobrescribe lo que
el contador haya editado en la base. Con --forzar sobrescribe desde los CSV.

Uso:
    uv run python scripts/sembrar_puc.py
    uv run python scripts/sembrar_puc.py --forzar
"""

import argparse

from agente.config import get_settings
from agente.infrastructure.db.repositorio import ResumenSiembra, sembrar_desde_csv


def _informe(resumen: ResumenSiembra, forzar: bool) -> str:
    def detalle(insertadas: int, sobrescritas: int, omitidas: int) -> str:
        partes = [f"{insertadas} insertadas"]
        if forzar:
            partes.append(f"{sobrescritas} sobrescritas")
        partes.append(f"{omitidas} omitidas")
        return ", ".join(partes)

    lineas = [
        f"Cuentas: {detalle(resumen.cuentas_insertadas, resumen.cuentas_sobrescritas, resumen.cuentas_omitidas)}.",
        f"Reglas: {detalle(resumen.reglas_insertadas, resumen.reglas_sobrescritas, resumen.reglas_omitidas)}.",
        f"Total: {resumen.insertadas} filas insertadas, "
        + (f"{resumen.sobrescritas} sobrescritas, " if forzar else "")
        + f"{resumen.omitidas} omitidas.",
    ]
    return "\n".join(lineas)


def main(argv: list[str] | None = None) -> ResumenSiembra:
    parser = argparse.ArgumentParser(description="Carga data/puc/*.csv en PostgreSQL.")
    parser.add_argument(
        "--forzar",
        action="store_true",
        help="sobrescribe las cuentas y reglas existentes con lo de los CSV (pierde las ediciones del contador)",
    )
    args = parser.parse_args(argv)
    resumen = sembrar_desde_csv(get_settings().directorio_puc, forzar=args.forzar)
    print(_informe(resumen, args.forzar))
    return resumen


if __name__ == "__main__":
    main()
