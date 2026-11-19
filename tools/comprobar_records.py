#!/usr/bin/env python3
"""Comprueba que nuestros <record> traen lo que el modelo de OTRO exige.

POR QUE EXISTE. El 21-10-2026 la instalacion de `gts_alma_creditos` reviento
DOS VECES SEGUIDAS en la instancia de un cliente, por la misma causa con dos
caras:

    null value in column "provider_id" ... violates not-null constraint
    La ventana de contexto debe ser un entero positivo.

Las dos salen de un <record> que crea un registro de un modelo de MuK sin los
campos que ese modelo exige. Y las dos aparecen SOLO al instalar: ni los
linters ni la sintaxis del XML las ven.

Hay una tercera cara, la del 19-10, que ya vigila `comprobar_instalacion.py`:
un <record> que nombra un campo que NO existe. Son espejo una de otra —sobra
un campo / falta un campo— y juntas cubren el fallo entero.

QUE MIRA, leyendo el fuente del modulo ajeno:

  1. Campos `required=True` SIN `default=` y sin `compute`/`related`: el
     <function> que los rellenaria corre DESPUES de crear, asi que no llega.
  2. Campos que aparecen en un `@api.constrains`: si el validador los
     compara contra algo, el valor de fabrica puede no pasar.

USO:
    python3 tools/comprobar_records.py <fichero.xml> --fuente <model.py> [...]
"""

import argparse
import re
import sys
import xml.etree.ElementTree as ET


def campos_del_modelo(fuentes):
    """Devuelve (obligatorios_sin_defecto, campos_validados) leyendo el fuente."""
    obligatorios, validados = {}, {}
    for ruta in fuentes:
        src = open(ruta, encoding="utf-8").read()
        modelo = re.search(r"_(?:name|inherit)\s*=\s*['\"]([\w.]+)['\"]", src)
        modelo = modelo.group(1) if modelo else ruta
        for m in re.finditer(r"\n    (\w+) = fields\.(\w+)\((.*?)\n    \)", src, re.S):
            nombre, _tipo, cuerpo = m.groups()
            if "required=True" not in cuerpo:
                continue
            if "default=" in cuerpo or "compute=" in cuerpo or "related=" in cuerpo:
                continue
            obligatorios.setdefault(modelo, set()).add(nombre)
        for m in re.finditer(r"@api\.constrains\(([^)]*)\)", src, re.S):
            for campo in re.findall(r"['\"](\w+)['\"]", m.group(1)):
                validados.setdefault(modelo, set()).add(campo)
    return obligatorios, validados


def main():
    p = argparse.ArgumentParser()
    p.add_argument("xml", nargs="+")
    p.add_argument("--fuente", nargs="+", required=True)
    a = p.parse_args()

    obligatorios, validados = campos_del_modelo(a.fuente)
    fallos = 0
    for ruta in a.xml:
        for record in ET.parse(ruta).getroot().iter("record"):
            modelo = record.get("model")
            xmlid = record.get("id", "")
            # Un <record> cuyo id lleva prefijo de OTRO modulo no CREA nada:
            # modifica un registro que ya existe, asi que no le aplican los
            # obligatorios. Es el caso de `muk_ai.provider_google`.
            if "." in xmlid:
                continue
            puestos = {f.get("name") for f in record.findall("field")}
            faltan = obligatorios.get(modelo, set()) - puestos
            ojo = validados.get(modelo, set()) - puestos
            if faltan:
                fallos += 1
                print(f'  GRAVE  {ruta}: <record id="{xmlid}"> de {modelo}')
                print(
                    "         faltan obligatorios sin defecto:",
                    ", ".join(sorted(faltan)),
                )
                print(
                    "         -> el INSERT falla; ponerlos en el XML,",
                    "no en un <function>",
                )
            if ojo:
                print(f'  aviso  {ruta}: <record id="{xmlid}"> de {modelo}')
                print(
                    "         no pone campos que valida un @api.constrains:",
                    ", ".join(sorted(ojo)),
                )
                print(
                    "         -> comprobar que el valor de fabrica pasa",
                    "la validacion",
                )
    if not fallos:
        print("Ningun <record> al que le falte un campo obligatorio.")
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main())
