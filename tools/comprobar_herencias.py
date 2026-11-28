#!/usr/bin/env python3
"""Un xpath a una vista heredada que apunta a algo que NO existe.

POR QUE EXISTE. El 18-11-2026 escribi `<filter name="type_company"
position="after">` sobre la vista de busqueda de contactos. Ese filtro NO
existe en la 19. Lo pillé mirando el fuente a mano, pero los once
comprobadores lo daban por bueno: ninguno mira dentro de la vista del padre.
Un anclaje inexistente **rompe la instalacion entera**, y el error no dice
que el culpable sea el anclaje.

QUE HACE. Por cada vista nuestra con `inherit_id`, busca esa vista en el
fuente de referencia y comprueba que cada anclaje -el elemento con
`position=`- existe de verdad.

LA CADENA DE HERENCIA, Y LA TRAMPA DE VERDAD. Un anclaje puede venir de otra
vista que tambien hereda del mismo padre. Eso vale **solo si el modulo que la
trae esta en nuestro `depends`**. Si no, el anclaje existe en la instancia
donde probamos y NO en la del siguiente cliente.

Me paso con ese mismo `type_company`: lo trae `hr_calendar`, que esta
instalado en la staging de COPYE. Habria funcionado en todas las pruebas y
reventado en el primer cliente sin ese modulo. Un fallo que las pruebas no
ven es peor que uno que las rompe.
"""

import os
import pathlib
import re
import sys
import xml.etree.ElementTree as ET

# Rama 18.0: el fuente de la 18 (en la rama 19.0, ~/ref/odoo).
REF = pathlib.Path(os.path.expanduser("~/ref/odoo-18.0"))
# Nuestros propios modulos, que son hermanos en el repo.
REPO = pathlib.Path(__file__).resolve().parent.parent


def raices(modulo):
    """Donde puede vivir un modulo: el repo, y el fuente de referencia."""
    return (REPO / modulo, REF / "addons" / modulo, REF / "odoo" / "addons" / modulo)


def conocido(modulo):
    """Si no esta en ningun sitio es de Enterprise: no me invento un fallo."""
    return any(r.is_dir() for r in raices(modulo))


def arch_de(xmlid, cache={}):
    """El <arch> de una vista del fuente de referencia, por su xmlid."""
    if xmlid in cache:
        return cache[xmlid]
    modulo, _, nombre = xmlid.partition(".")
    cache[xmlid] = None
    patron = re.compile(r'<record[^>]+id="%s"[^>]*>' % re.escape(nombre))
    for base in raices(modulo):
        if not base.is_dir():
            continue
        for f in base.rglob("*.xml"):
            texto = f.read_text(encoding="utf-8", errors="replace")
            if not patron.search(texto):
                continue
            try:
                raiz = ET.parse(f).getroot()
            except ET.ParseError:
                continue
            for rec in raiz.iter("record"):
                if rec.get("id") == nombre and rec.get("model") == "ir.ui.view":
                    cache[xmlid] = rec.find("field[@name='arch']")
                    return cache[xmlid]
    return cache[xmlid]


def depends_del_modulo(ruta):
    """Lo que declara el manifiesto, mas lo que arrastran por debajo."""
    man = pathlib.Path(ruta) / "__manifest__.py"
    if not man.is_file():
        return set()
    import ast as _ast

    datos = _ast.literal_eval(man.read_text(encoding="utf-8"))
    pendientes, vistos = list(datos.get("depends", [])), set()
    while pendientes:
        m = pendientes.pop()
        if m in vistos:
            continue
        vistos.add(m)
        for base in (REF / "addons" / m, REF / "odoo" / "addons" / m):
            f = base / "__manifest__.py"
            if f.is_file():
                try:
                    pendientes += _ast.literal_eval(f.read_text(encoding="utf-8")).get(
                        "depends", []
                    )
                except Exception:
                    pass
    return vistos


def hermanas(xmlid):
    """Las vistas del fuente que heredan del mismo padre, con SU modulo."""
    archs = []
    _, _, nombre = xmlid.partition(".")
    patron = re.compile(r'name="inherit_id"[^>]*ref="(?:\w+\.)?%s"' % re.escape(nombre))
    for base in (REF / "addons", REF / "odoo" / "addons"):
        for f in base.rglob("*.xml"):
            texto = f.read_text(encoding="utf-8", errors="replace")
            if not patron.search(texto):
                continue
            try:
                raiz = ET.parse(f).getroot()
            except ET.ParseError:
                continue
            for rec in raiz.iter("record"):
                h = rec.find("field[@name='inherit_id']")
                a = rec.find("field[@name='arch']")
                if (
                    h is not None
                    and a is not None
                    and (h.get("ref") or "").endswith(nombre)
                ):
                    archs.append((a, f.parts[f.parts.index("addons") + 1]))
    return archs


def existe(anclaje, archs):
    """Devuelve el modulo donde esta el anclaje, o None. '' = el propio padre."""
    etiqueta, nombre, expr = anclaje
    for arch in archs:
        modulo = ""
        if isinstance(arch, tuple):
            arch, modulo = arch
        if arch is None:
            continue
        if expr is not None:
            # EVALUAR el xpath, no compararlo como texto. La primera version
            # lo comparaba, y daba por inexistente un //div[@class='oe_title']
            # que si existe: un comprobador que se queja de codigo correcto
            # acaba desactivado.
            consulta = expr
            if consulta.startswith("//"):
                consulta = "." + consulta
            try:
                if arch.findall(consulta):
                    return modulo
            except SyntaxError:
                # ElementTree solo entiende un subconjunto de xpath; si no
                # sabe evaluarlo, no se inventa un fallo.
                return modulo
            continue
        for e in arch.iter():
            if e.tag == etiqueta and e.get("name") == nombre:
                return modulo
    return None


def anclajes(arch):
    for e in arch.iter():
        if not e.get("position"):
            continue
        if e.tag == "xpath":
            yield ("xpath", None, e.get("expr"))
        else:
            yield (e.tag, e.get("name"), None)


def main(ruta):
    fallos = 0
    sin_comprobar = set()
    depends = depends_del_modulo(ruta)
    for f in sorted(pathlib.Path(ruta).rglob("*.xml")):
        try:
            raiz = ET.parse(f).getroot()
        except ET.ParseError as e:
            # Saltarselo en silencio deja pasar un fichero roto.
            print(f"{f.name}: no parsea ({e})")
            fallos += 1
            continue
        for rec in raiz.iter("record"):
            if rec.get("model") != "ir.ui.view":
                continue
            h = rec.find("field[@name='inherit_id']")
            arch = rec.find("field[@name='arch']")
            if h is None or arch is None or not h.get("ref"):
                continue
            padre = h.get("ref")
            modulo_padre = padre.partition(".")[0]
            if not conocido(modulo_padre):
                # Enterprise: no lo tengo y no me lo invento.
                sin_comprobar.add(modulo_padre)
                continue
            archs = [arch_de(padre)]
            if archs[0] is None:
                print(f"{f.name}: no encuentro la vista padre «{padre}»")
                fallos += 1
                continue
            for anc in anclajes(arch):
                if existe(anc, archs) is not None:
                    continue
                if len(archs) == 1:  # solo si hace falta
                    archs += hermanas(padre)
                donde = existe(anc, archs)
                que = anc[2] if anc[2] else f'<{anc[0]} name="{anc[1]}">'
                if donde is None:
                    print(f"{f.name}: el anclaje {que} no existe en «{padre}»")
                    fallos += 1
                elif donde and donde not in depends:
                    print(
                        f"{f.name}: el anclaje {que} NO lo trae «{padre}» sino el "
                        f"módulo «{donde}», que no está en depends. Funcionará donde "
                        f"esté instalado y romperá donde no."
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
