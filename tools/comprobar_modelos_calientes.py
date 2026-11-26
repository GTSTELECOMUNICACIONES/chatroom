"""Ningún campo GUARDADO nuevo en los modelos que Odoo lee en cada petición.

El 26-11-2026 la 0.15.0 añadió un Char a `res.users`: COPYE hizo el pull,
la columna no existía todavía y la instancia entera dio 500 (web y crons),
sin poder ni actualizar desde la web, porque Odoo lee el usuario antes de
nada. Lo que necesite guardarse sobre un usuario, un contacto o la compañía
va en un modelo propio que apunte a ellos.

Uso: comprobar_modelos_calientes.py <modulo>
"""

import ast
import pathlib
import sys

CALIENTES = {"res.users", "res.partner", "res.company", "res.users.settings"}


def guardado(llamada):
    """¿El campo se guarda? Calculado sin store=True o related sin store no."""
    claves = {k.arg: k.value for k in llamada.keywords if k.arg}
    if "store" in claves:
        return isinstance(claves["store"], ast.Constant) and bool(claves["store"].value)
    return not ("compute" in claves or "related" in claves)


def main(ruta):
    malos = []
    for py in sorted((pathlib.Path(ruta) / "models").glob("*.py")):
        for clase in ast.walk(ast.parse(py.read_text())):
            if not isinstance(clase, ast.ClassDef):
                continue
            heredados = set()
            for c in clase.body:
                if (
                    isinstance(c, ast.Assign)
                    and isinstance(c.targets[0], ast.Name)
                    and c.targets[0].id == "_inherit"
                ):
                    # `_inherit = "res.partner"` y también la forma de lista.
                    valores = (
                        c.value.elts if isinstance(c.value, ast.List) else [c.value]
                    )
                    heredados |= {
                        v.value for v in valores if isinstance(v, ast.Constant)
                    }
            calientes = heredados & CALIENTES
            if not calientes:
                continue
            hereda = ", ".join(sorted(calientes))
            for c in clase.body:
                if (
                    isinstance(c, ast.Assign)
                    and isinstance(c.value, ast.Call)
                    and "fields." in ast.unparse(c.value.func)
                    and guardado(c.value)
                ):
                    malos.append(f"{py.name}:{c.lineno}: {hereda}.{c.targets[0].id}")
    for m in malos:
        print(
            f"{m} es un campo GUARDADO en un modelo que se lee en cada "
            "petición: un pull sin actualizar tumba la instancia. Va en un "
            "modelo propio."
        )
    return 1 if malos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))
