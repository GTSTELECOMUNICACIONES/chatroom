#!/usr/bin/env python3
"""Caza el mismo x2many declarado dos veces en un formulario.

POR QUE. El cliente fusiona las dos declaraciones con `patchActiveFields` y
da por hecho que la primera trae subvista. Si no, revienta con un
`TypeError: Cannot read properties of undefined` en `_checkValidity` al tocar
CUALQUIER cosa del formulario —el statusbar, por ejemplo—, sin nombrar el
campo ni la pestaña, y como es JavaScript no deja rastro en el servidor.

Me costo media tarde en el piloto de abogados. La salida limpia es un campo
calculado propio, declarado una sola vez.

No se miran los <field> que estan DENTRO de una subvista: ahi hablan de otro
modelo y repetirse es normal.
"""

import pathlib
import sys
import xml.etree.ElementTree as ET

ANIDADAS = ("list", "tree", "form", "kanban", "calendar", "search")


def campos_de_primer_nivel(form):
    """Los <field> del formulario, sin entrar en sus subvistas."""
    dentro = set()
    for campo in form.iter("field"):
        for vista in ANIDADAS:
            for sub in campo.iter(vista):
                dentro.update(id(x) for x in sub.iter())
    return [c for c in form.iter("field") if id(c) not in dentro]


def main(ruta):
    fallos = 0
    for fichero in sorted(pathlib.Path(ruta).rglob("*.xml")):
        try:
            raiz = ET.parse(fichero).getroot()
        except ET.ParseError:
            continue
        for form in raiz.iter("form"):
            vistos = {}
            for campo in campos_de_primer_nivel(form):
                nombre = campo.get("name")
                if not nombre:
                    continue
                # Solo molesta en los relacionales: un Char repetido es feo
                # pero no rompe nada.
                relacional = campo.find("list") is not None or campo.get(
                    "widget"
                ) in ("many2many_tags", "one2many", "many2many")
                vistos.setdefault(nombre, []).append(relacional)
            for nombre, apariciones in vistos.items():
                if len(apariciones) > 1 and any(apariciones):
                    print(
                        "%s: «%s» declarado %s veces en el mismo <form>. Usa "
                        "un campo calculado aparte para la segunda."
                        % (fichero.name, nombre, len(apariciones))
                    )
                    fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
