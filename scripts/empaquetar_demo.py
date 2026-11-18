"""Empaqueta el dominio y el parser en docs/agente_demo.zip para correrlos en el navegador (Pyodide)."""

import shutil
import zipfile
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[1]
DOCS = RAIZ / "docs"
EXCLUIR = {"lector_pdf.py", "lector_carpeta.py"}  # dependen de pypdf, no van al navegador
INCLUIR = [
    (RAIZ / "src" / "agente" / "domain", RAIZ / "src"),
    (RAIZ / "src" / "agente" / "infrastructure" / "dian", RAIZ / "src"),
    (RAIZ / "data" / "puc", RAIZ),
]


def main() -> None:
    DOCS.mkdir(exist_ok=True)
    destino = DOCS / "agente_demo.zip"
    with zipfile.ZipFile(destino, "w", zipfile.ZIP_DEFLATED) as z:
        for base, relativo_a in INCLUIR:
            for f in base.rglob("*"):
                if f.is_file() and f.suffix != ".pyc" and "__pycache__" not in f.parts and f.name not in EXCLUIR:
                    z.write(f, f.relative_to(relativo_a).as_posix())
    shutil.copy(RAIZ / "tests" / "fixtures" / "factura_comercio_contado.xml", DOCS / "ejemplo.xml")
    print(f"Listo: {destino}")


if __name__ == "__main__":
    main()