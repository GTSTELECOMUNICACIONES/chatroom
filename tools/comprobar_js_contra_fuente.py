#!/usr/bin/env python3
"""El JS y las plantillas OWL del modulo, contra el fuente de Odoo de SU rama.

POR QUE EXISTE (28-11-2026, port del ChatRoom a la 18). En la 18 la herencia
de plantillas OWL se resuelve EN EL NAVEGADOR: un xpath que no casa, o un
import que no existe, deja el backend en blanco sin un solo error en el
servidor (ya paso dos veces: ver la memoria «backend en blanco»). Ningun
linter lo mira. Esto comprueba, contra ~/ref/odoo-18.0 (o la ruta de
GTS_ODOO_REF):

1. Cada `import { A, B } from "@mod/ruta"` de un modulo de Odoo: que el
   fichero exista y que exporte A y B.
2. Cada `t-inherit="x" t-inherit-mode="extension"`: que la plantilla x exista
   y que cada `xpath` de la herencia case con algun nodo.
3. Cada `patch(Clase.prototype, { metodo() / get x() })`: que el metodo o el
   getter aparezca en el fichero de la clase o en un parche de Odoo a ella.
   (Heuristico: busca el nombre; un falso «ok» es posible, un fallo no.)

Lo que no puede mirar se imprime con «(sin comprobar…)»: un hueco silencioso
se lee como cobertura.
"""

import os
import pathlib
import re
import sys

from lxml import etree

REF = pathlib.Path(os.environ.get("GTS_ODOO_REF", os.path.expanduser("~/ref/odoo-18.0")))
ADDONS = [REF / "addons", REF / "odoo" / "addons"]

RE_IMPORT = re.compile(r'import\s*\{([^}]*)\}\s*from\s*"@([a-z_]+)/([^"]+)"', re.S)
RE_PATCH = re.compile(r"patch\((\w+)\.prototype,\s*\{")


def fichero_de(modulo, ruta):
    for base in ADDONS:
        for ext in (".js", "/index.js"):
            f = base / modulo / "static" / "src" / (ruta + ext)
            if f.is_file():
                return f
    return None


def exporta(f, nombre, vistos=None):
    vistos = vistos or set()
    if f in vistos:
        return False
    vistos.add(f)
    texto = f.read_text(encoding="utf-8")
    if re.search(
        r"export\s+(?:const|let|class|function|async function)\s+%s\b" % re.escape(nombre),
        texto,
    ):
        return True
    if re.search(r"export\s*\{[^}]*\b%s\b[^}]*\}" % re.escape(nombre), texto):
        return True
    for origen in re.findall(r'export\s*\*\s*from\s*"([^"]+)"', texto):
        if origen.startswith("."):
            g = (f.parent / origen).resolve()
            g = g.with_suffix(".js") if g.suffix != ".js" else g
        elif origen.startswith("@"):
            mod, _, ruta = origen[1:].partition("/")
            g = fichero_de(mod, ruta)
        else:
            g = None
        if g and g.is_file() and exporta(g, nombre, vistos):
            return True
    return False


def plantillas_de_odoo():
    """{t-name: elemento} de todas las plantillas OWL del fuente."""
    todas = {}
    for base in ADDONS:
        for f in base.glob("*/static/src/**/*.xml"):
            try:
                arbol = etree.parse(str(f))
            except etree.XMLSyntaxError:
                continue
            for t in arbol.iter("t"):
                nombre = t.get("t-name")
                if nombre:
                    todas[nombre] = t
    return todas


def main(modulo):
    fallos, huecos = [], set()
    raiz = pathlib.Path(modulo)
    clases = {}
    for js in sorted(raiz.glob("static/src/**/*.js")):
        texto = js.read_text(encoding="utf-8")
        for nombres, mod, ruta in RE_IMPORT.findall(texto):
            f = fichero_de(mod, ruta)
            if f is None:
                if (ADDONS[0] / mod).is_dir() or (ADDONS[1] / mod).is_dir():
                    fallos.append(f"{js.name}: @{mod}/{ruta} no existe en {REF.name}")
                else:
                    huecos.add(mod)
                continue
            for nombre in [n.strip() for n in nombres.split(",") if n.strip()]:
                nombre = nombre.split(" as ")[0].strip()
                if not exporta(f, nombre):
                    fallos.append(f"{js.name}: @{mod}/{ruta} no exporta {nombre}")
                else:
                    clases[nombre] = f
        for clase, inicio in [(m.group(1), m.end()) for m in RE_PATCH.finditer(texto)]:
            f = clases.get(clase)
            if f is None:
                continue
            # Los miembros de primer nivel del objeto del parche.
            cuerpo, nivel, i = [], 1, inicio
            while i < len(texto) and nivel:
                c = texto[i]
                nivel += c == "{"
                nivel -= c == "}"
                cuerpo.append(c if nivel == 1 else ("\n" if c == "\n" else " "))
                i += 1
            miembros = re.findall(
                r"^\s{4}(?:async\s+)?(?:get\s+|set\s+)?(\w+)\s*\(",
                "".join(cuerpo),
                re.M,
            )
            fuente = f.read_text(encoding="utf-8")
            # Tambien los parches de Odoo a esa clase (p. ej. thread_model_patch).
            for base in ADDONS:
                for g in base.glob("*/static/src/**/*patch*.js"):
                    tg = g.read_text(encoding="utf-8")
                    if re.search(r"patch\(%s[.\s,)]" % clase, tg):
                        fuente += tg
            for m in set(miembros):
                if m == "setup" or m.startswith(("gts", "_gts")):
                    continue
                if not re.search(r"\b%s\b" % re.escape(m), fuente):
                    fallos.append(f"{js.name}: {clase} no tiene «{m}» en {REF.name}")
    plantillas = None
    for xml in sorted(raiz.glob("static/src/**/*.xml")):
        arbol = etree.parse(str(xml))
        for t in arbol.iter("t"):
            padre = t.get("t-inherit")
            if not padre or t.get("t-inherit-mode", "primary") != "extension":
                continue
            if plantillas is None:
                plantillas = plantillas_de_odoo()
            original = plantillas.get(padre)
            if original is None:
                fallos.append(f"{xml.name}: la plantilla {padre} no existe en {REF.name}")
                continue
            for xp in t.iter("xpath"):
                if not original.xpath(xp.get("expr").lstrip("/").join([".//", ""])
                                      if xp.get("expr").startswith("//")
                                      else xp.get("expr")):
                    fallos.append(f"{xml.name}: {padre}: el xpath {xp.get('expr')} no casa")
    for f in fallos:
        print(f)
    huecos.discard("odoo")  # @odoo/owl: la libreria OWL, no un modulo
    for h in sorted(huecos):
        print(f"   (sin comprobar, no está en {REF.name}: {h})")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(max(main(m) for m in sys.argv[1:]) if len(sys.argv) > 1 else 2)
