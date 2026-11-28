#!/usr/bin/env python3
"""Un xmlid ajeno que no existe: menus que apuntan a una accion inventada.

POR QUE EXISTE. El 18-11-2026 escribi cuatro entradas de menu apuntando a
acciones estandar de Odoo. Ninguno de los doce comprobadores mira eso, y un
`action="project.lo_que_sea"` que no existe **rompe la instalacion entera**.

Y hay una razon mas: ese mismo dia me equivoque DOS VECES comprobandolo a
mano con `grep` -una vez por el orden de los atributos y otra por incluir una
carpeta que no existia, que hace que grep devuelva error-. Las dos veces lei
el vacio como una ausencia y estuve a punto de cambiar codigo que funcionaba.
**Lo que comprueba no soy yo leyendo: es esto corriendo.**

QUE MIRA. Todo `ref=` y todo `action=` que nombre un modulo distinto del
nuestro, contra el fuente de referencia. Los del propio modulo no se miran
aqui: de eso ya se encarga `comprobar_instalacion.py`.
"""

import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

# Rama 18.0: el fuente de la 18 (en la rama 19.0, ~/ref/odoo).
REF = pathlib.Path(os.path.expanduser("~/ref/odoo-18.0"))
REPO = pathlib.Path(__file__).resolve().parent.parent


def raices(modulo):
    return (REPO / modulo, REF / "addons" / modulo, REF / "odoo" / "addons" / modulo)


XMLID = re.compile(r"^([a-z0-9_]+)\.([a-zA-Z0-9_]+)$")


def existe(modulo, nombre, cache={}):
    clave = (modulo, nombre)
    if clave in cache:
        return cache[clave]
    cache[clave] = False
    patron = re.compile(r'\bid="%s"' % re.escape(nombre))
    for base in raices(modulo):
        if not base.is_dir():
            continue
        for f in base.rglob("*.xml"):
            if patron.search(f.read_text(encoding="utf-8", errors="replace")):
                cache[clave] = True
                return True
        # tambien pueden venir de un CSV de datos
        for f in base.rglob("*.csv"):
            if nombre in f.read_text(encoding="utf-8", errors="replace"):
                cache[clave] = True
                return True
    return cache[clave]


def main(ruta):
    mio = pathlib.Path(ruta).name
    fallos = 0
    sin_comprobar = set()
    for f in sorted(pathlib.Path(ruta).rglob("*.xml")):
        try:
            raiz = ET.parse(f).getroot()
        except ET.ParseError as e:
            print(f"{f.name}: no parsea ({e})")
            fallos += 1
            continue
        for e in raiz.iter():
            for atributo in ("ref", "action"):
                valor = e.get(atributo)
                if not valor:
                    continue
                casa = XMLID.match(valor)
                if not casa:
                    continue
                modulo, nombre = casa.groups()
                if modulo == mio:
                    continue
                if not any(r.is_dir() for r in raices(modulo)):
                    # Enterprise: no lo tengo y no me lo invento.
                    sin_comprobar.add(modulo)
                    continue
                if not existe(modulo, nombre):
                    print(
                        f'{f.name}: <{e.tag} {atributo}="{valor}"> no existe '
                        f"en el módulo «{modulo}»"
                    )
                    fallos += 1
    if sin_comprobar:
        print(
            "   (sin comprobar, no están en ~/ref: %s)"
            % ", ".join(sorted(sin_comprobar))
        )
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
