#!/usr/bin/env python3
"""Caza decoradores `@api.depends` que no estan sobre un compute.

EL CASO QUE LO PROVOCO, 25-09-2026. Al meter un metodo ayudante entre un
`@api.depends` y su compute, el decorador se quedo colgando del ayudante:

    @api.depends("sale_line_id.order_id.order_line.qty_delivered", ...)
    @api.model
    def _importe_previsto(self, linea):      # <- un AYUDANTE, no un compute
        ...

    def _compute_economia(self):             # <- el compute, SIN dependencias
        ...

Odoo no mira los `depends` de un metodo que no es compute, asi que
`_compute_economia` se quedo sin ninguna. Y como sus campos son ALMACENADOS, se
calcularon al crear el expediente y **no se volvieron a mover nunca**.

Lo caro es como se manifiesta: en pantalla, «9 horas imputadas» y «0,00 EUR de
honorarios» al lado. Los numeros no estaban mal calculados; eran de antes. Ni
Python ni Odoo dan el menor aviso, y en el log no aparece nada.

Uso:
    python3 tools/comprobar_api_depends.py

Sale con codigo 1 si encuentra alguno. Pasarlo cuando se muevan metodos de
sitio, que es cuando pasa.
"""

import ast
import os
import sys

# Un `@api.depends` solo tiene sentido sobre el metodo de un campo calculado.
# En Odoo esos se llaman `_compute_*` por convencion, y el marco lo asume.
PREFIJO_COMPUTE = "_compute_"

# `depends_context` es otra cosa: vale en cualquier metodo cacheado.
DECORADOR = "depends"


def _es_api_depends(nodo):
    """True si el decorador es exactamente `@api.depends(...)`."""
    funcion = nodo.func if isinstance(nodo, ast.Call) else nodo
    return (
        isinstance(funcion, ast.Attribute)
        and funcion.attr == DECORADOR
        and isinstance(funcion.value, ast.Name)
        and funcion.value.id == "api"
    )


def revisar(raiz):
    fallos = []
    for carpeta, _subs, ficheros in os.walk(raiz):
        if ".git" in carpeta or "__pycache__" in carpeta or "/tools" in carpeta:
            continue
        for nombre in ficheros:
            if not nombre.endswith(".py"):
                continue
            ruta = os.path.join(carpeta, nombre)
            try:
                with open(ruta, encoding="utf-8") as fichero:
                    arbol = ast.parse(fichero.read())
            except SyntaxError:
                continue
            for nodo in ast.walk(arbol):
                if not isinstance(nodo, ast.FunctionDef):
                    continue
                if nodo.name.startswith(PREFIJO_COMPUTE):
                    continue
                for decorador in nodo.decorator_list:
                    if _es_api_depends(decorador):
                        fallos.append(
                            (os.path.relpath(ruta, raiz), nodo.lineno, nodo.name)
                        )
    return fallos


def main():
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallos = revisar(raiz)
    if not fallos:
        print("Ningún @api.depends huérfano.")
        return 0

    print(f"{len(fallos)} @api.depends sobre algo que NO es un compute:\n")
    for ruta, linea, metodo in fallos:
        print(f"  {ruta}:{linea}  ->  {metodo}()")
    print(
        "\nOdoo IGNORA esos depends. Si el compute de al lado se quedó sin los\n"
        "suyos y sus campos son almacenados, dejan de recalcularse: enseñan el\n"
        "valor del día que se creó el registro y nadie avisa."
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
