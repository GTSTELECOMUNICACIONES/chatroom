#!/usr/bin/env python3
"""Caza dominios sobre campos calculados que no se pueden buscar.

POR QUE EXISTE. Un campo `compute` sin `store=True` ni `search=` NO ESTA EN LA
BASE DE DATOS. Un filtro que lo use pasa ruff, black, pylint-odoo, la
validacion del XML y la instalacion entera —y revienta el dia que alguien
pulsa el filtro, que es siempre delante de un cliente.

Me paso el 16-11-2026 con `dias_sin_venir` en la ficha del alumno.

La salida es el nombre del fichero, la linea y el campo. Devuelve 1 si hay
alguno.
"""

import ast
import pathlib
import re
import sys


def campos_sin_busqueda(raiz):
    """Los campos `compute` que no se pueden usar en un dominio."""
    sospechosos = {}
    for fichero in (raiz / "models").glob("*.py"):
        arbol = ast.parse(fichero.read_text())
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, ast.Assign):
                continue
            if not isinstance(nodo.value, ast.Call):
                continue
            if "fields." not in ast.unparse(nodo.value.func):
                continue
            destino = nodo.targets[0]
            if not isinstance(destino, ast.Name):
                continue
            claves = {k.arg for k in nodo.value.keywords}
            # `store` y `search` son las dos formas de hacerlo buscable. Un
            # `related` sin store tampoco lo es, pero Odoo si sabe cruzarlo,
            # asi que no se marca.
            if "compute" in claves and not claves & {"store", "search"}:
                sospechosos[destino.id] = fichero.name
    return sospechosos


def main(ruta):
    raiz = pathlib.Path(ruta)
    sospechosos = campos_sin_busqueda(raiz)
    fallos = 0
    for fichero in sorted((raiz / "views").glob("*.xml")):
        lineas = fichero.read_text().splitlines()
        for numero, linea in enumerate(lineas, 1):
            # El dominio puede ocupar varias lineas; se mira la que abre y
            # las dos siguientes, que es como se parten en la practica.
            if "domain=" not in linea:
                continue
            trozo = " ".join(lineas[numero - 1 : numero + 2])
            for campo, origen in sospechosos.items():
                if re.search(r"['\"]%s['\"]\s*," % re.escape(campo), trozo):
                    print(
                        "%s:%s: dominio sobre «%s» (%s), que es un cálculo "
                        "sin `store=True` ni `search=`. El filtro reventará "
                        "al pulsarlo." % (fichero.name, numero, campo, origen)
                    )
                    fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
