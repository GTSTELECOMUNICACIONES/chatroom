#!/usr/bin/env python3
"""Un One2many cuyo inverso NO existe en el otro modelo.

POR QUE EXISTE. El 18-11-2026, con el modulo ya subido y David dandole a
instalar, la instalacion reviento con:

    ValueError: 'tipo_obra_id' declared in 'gts.obra.tipo.obra_ids'
    does not exist on 'project.project'.

Habia renombrado el campo a `obra_tipo_id` en el modelo y en las vistas, y me
deje el inverso del One2many con el nombre viejo. **Los trece comprobadores lo
daban por bueno**, porque ninguno miraba al otro lado de la relacion. Solo se
ve al construir el registro, o sea, al instalar, o sea, delante del cliente.

QUE MIRA. Cada `fields.One2many("modelo", "inverso", ...)` de nuestros
modelos: que `inverso` exista en `modelo`, mirando primero lo que declaramos
nosotros y despues el fuente de referencia. Si el modelo no esta en ninguno de
los dos, se calla: un comprobador que se queja de lo que no puede saber acaba
desactivado.
"""

import ast
import os
import pathlib
import re
import sys

# Rama 18.0: el fuente de la 18 (en la rama 19.0, ~/ref/odoo).
REF = pathlib.Path(os.path.expanduser("~/ref/odoo-18.0"))


def campos_declarados(ruta):
    """{modelo: {campos}} de lo que declara nuestro modulo, propio o heredado."""
    modelos = {}
    for py in sorted(pathlib.Path(ruta).rglob("*.py")):
        try:
            arbol = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(arbol):
            if not isinstance(n, ast.ClassDef):
                continue
            nombres, campos = [], set()
            for c in n.body:
                if not (
                    isinstance(c, ast.Assign) and isinstance(c.targets[0], ast.Name)
                ):
                    continue
                destino = c.targets[0].id
                if destino in ("_name", "_inherit"):
                    v = c.value
                    if isinstance(v, ast.Constant):
                        nombres.append(v.value)
                    elif isinstance(v, ast.List):
                        nombres += [
                            e.value for e in v.elts if isinstance(e, ast.Constant)
                        ]
                elif isinstance(c.value, ast.Call) and "fields." in ast.unparse(
                    c.value.func
                ):
                    campos.add(destino)
            for nombre in nombres:
                modelos.setdefault(nombre, set()).update(campos)
    return modelos


def en_el_fuente(modelo, campo, cache={}):
    """Busca el campo en los ficheros del fuente que definen ese modelo."""
    clave = (modelo, campo)
    if clave in cache:
        return cache[clave]
    cache[clave] = None  # None = no se ha encontrado el modelo
    declara = re.compile(r"_(?:name|inherit)\s*=\s*[\"'](%s)[\"']" % re.escape(modelo))
    tiene = re.compile(r"^\s+%s\s*=\s*fields\." % re.escape(campo), re.M)
    for base in (REF / "addons", REF / "odoo" / "addons"):
        if not base.is_dir():
            continue
        for f in base.rglob("models/*.py"):
            texto = f.read_text(encoding="utf-8", errors="replace")
            if not declara.search(texto):
                continue
            if cache[clave] is None:
                cache[clave] = False  # el modelo existe, el campo aun no
            if tiene.search(texto):
                cache[clave] = True
                return True
    return cache[clave]


def uno_a_muchos(ruta):
    """(fichero, linea, comodelo, inverso) de cada One2many nuestro."""
    for py in sorted(pathlib.Path(ruta).rglob("*.py")):
        try:
            arbol = ast.parse(py.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for n in ast.walk(arbol):
            if not isinstance(n, ast.Call):
                continue
            if not ast.unparse(n.func).endswith("fields.One2many"):
                continue
            posicionales = [a for a in n.args if isinstance(a, ast.Constant)]
            por_nombre = {k.arg: k.value for k in n.keywords}
            comodelo = inverso = None
            if len(posicionales) >= 2:
                comodelo, inverso = posicionales[0].value, posicionales[1].value
            if isinstance(por_nombre.get("comodel_name"), ast.Constant):
                comodelo = por_nombre["comodel_name"].value
            if isinstance(por_nombre.get("inverse_name"), ast.Constant):
                inverso = por_nombre["inverse_name"].value
            if comodelo and inverso:
                yield py.name, n.lineno, comodelo, inverso


def main(ruta):
    nuestros = campos_declarados(ruta)
    fallos = 0
    for fichero, linea, comodelo, inverso in uno_a_muchos(ruta):
        if inverso in nuestros.get(comodelo, set()):
            continue
        hay = en_el_fuente(comodelo, inverso)
        if hay:
            continue
        if hay is None and comodelo not in nuestros:
            continue  # modelo de un módulo que no tengo: no me invento nada
        print(
            f"{fichero}:{linea}: el One2many apunta a «{inverso}», que NO existe "
            f"en «{comodelo}». La instalación reventará al construir el modelo."
        )
        fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
