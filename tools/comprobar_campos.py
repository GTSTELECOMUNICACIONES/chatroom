#!/usr/bin/env python3
"""Un campo que no existe en la vista, y un modelo sin ACL.

Las dos cosas pasan los linters y revientan al instalar o al abrir la pantalla.

LO QUE APRENDI AL ESCRIBIRLO, 17-11-2026. La primera version miraba TODOS los
<field> del arch y daba tres falsos positivos en `gts_obra`: `partner_id`,
`date_start` y `date`, que no son de `gts.obra` sino de `project.project`,
porque estan dentro de la <list> anidada del one2many `project_ids`. Un
comprobador que se queja de codigo correcto acaba desactivado; asi que ahora
solo mira los campos que cuelgan de la vista, no los de dentro de un x2many,
cuyo modelo es otro.
"""

import ast
import pathlib
import sys
import xml.etree.ElementTree as ET

MIXINS = {
    "image.mixin": {
        "image_1920",
        "image_1024",
        "image_512",
        "image_256",
        "image_128",
    },
    "mail.thread": {"message_ids", "message_follower_ids", "activity_ids"},
    "mail.activity.mixin": {
        "activity_ids",
        "activity_state",
        "activity_user_id",
        "activity_type_id",
        "activity_date_deadline",
        "activity_summary",
    },
}


def modelos_del_modulo(raiz):
    """{nombre del modelo: campos que declara} para los modelos PROPIOS."""
    modelos = {}
    for py in sorted((raiz / "models").glob("*.py")):
        for n in ast.walk(ast.parse(py.read_text())):
            if not isinstance(n, ast.ClassDef):
                continue
            nombre, campos, mix = None, {"id", "display_name"}, []
            for c in n.body:
                if not (
                    isinstance(c, ast.Assign) and isinstance(c.targets[0], ast.Name)
                ):
                    continue
                destino = c.targets[0].id
                if destino == "_name":
                    nombre = c.value.value
                elif destino == "_inherit":
                    v = c.value
                    mix = (
                        [e.value for e in v.elts]
                        if isinstance(v, ast.List)
                        else [v.value]
                    )
                elif isinstance(c.value, ast.Call) and "fields." in ast.unparse(
                    c.value.func
                ):
                    campos.add(destino)
            # Un AbstractModel no tiene tabla y no pide ACL: la OCA no la pone
            # a sus mail.gateway.<tipo> y Odoo no la comprueba sin CRUD.
            if any("AbstractModel" in ast.unparse(b) for b in n.bases):
                continue
            if nombre:
                for m in mix:
                    campos |= MIXINS.get(m, set())
                modelos[nombre] = campos
    return modelos


def campos_de_la_vista(arch):
    """Los <field> que son de ESTA vista, no los de dentro de un x2many.

    Un <field> dentro de otro <field> pertenece al modelo del x2many que lo
    contiene, que es otro, y aqui no se puede comprobar.
    """
    for hijo in arch:
        yield from _recorrer(hijo)


def _recorrer(nodo):
    if nodo.tag == "field":
        if nodo.get("name"):
            yield nodo.get("name")
        return  # no se baja: lo de dentro es de otro modelo
    for hijo in nodo:
        yield from _recorrer(hijo)


def main(ruta):
    raiz = pathlib.Path(ruta)
    modelos = modelos_del_modulo(raiz)
    if not modelos:
        print("Aviso: no se ha encontrado ningun modelo propio.")
        return 0
    malo = 0
    for f in sorted((raiz / "views").glob("*.xml")):
        for rec in ET.parse(f).getroot().iter("record"):
            if rec.get("model") != "ir.ui.view":
                continue
            modelo = rec.find("field[@name='model']")
            arch = rec.find("field[@name='arch']")
            if modelo is None or arch is None or modelo.text not in modelos:
                continue
            # una vista heredada toca campos del modelo de otro: no se juzga
            if rec.find("field[@name='inherit_id']") is not None:
                continue
            for nombre in campos_de_la_vista(arch):
                if nombre not in modelos[modelo.text]:
                    print(f"{f.name}: «{nombre}» no existe en {modelo.text}")
                    malo = 1
    csv = raiz / "security/ir.model.access.csv"
    acl = csv.read_text() if csv.exists() else ""
    for nombre in modelos:
        if "model_" + nombre.replace(".", "_") not in acl:
            print(f"{nombre} NO TIENE ir.model.access.csv")
            malo = 1
    return malo


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
