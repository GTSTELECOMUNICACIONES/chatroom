#!/usr/bin/env python3
"""Una vista heredada no puede buscar por un atributo TRADUCIBLE.

POR QUE EXISTE. El 19-11-2026 la instalacion de gts_lab_ensayos reviento en la
staging de COPYE con

    View inheritance may not use attribute 'string' as a selector.

por un `//field[@name='categ_id'][@string='Category']`. Pasaba los catorce
comprobadores. La regla esta en `ir_ui_view.py` (`_valid_inheritance`): un
`string` se traduce, asi que en una base en espanol ya no dice «Category» y
el selector no encontraria nada. Odoo lo prohibe para todos los atributos de
`odoo/tools/translate.py:TRANSLATED_ATTRS`, en un xpath y en un selector
directo (`<field string="..." position="...">`).

Uso:  python3 tools/comprobar_selectores.py <modulo>
"""

import pathlib
import re
import sys
import xml.etree.ElementTree as ET

# odoo/tools/translate.py, TRANSLATED_ATTRS (Odoo 19), sin los t-attf-*.
TRADUCIBLES = {
    "string",
    "add-label",
    "help",
    "sum",
    "avg",
    "confirm",
    "placeholder",
    "alt",
    "title",
    "aria-label",
    "aria-keyshortcuts",
    "aria-placeholder",
    "aria-roledescription",
    "aria-valuetext",
    "value_label",
    "data-tooltip",
    "label",
    "confirm-label",
    "confirm-title",
    "cancel-label",
}
EN_XPATH = re.compile(r"@(%s)\b" % "|".join(sorted(TRADUCIBLES, key=len, reverse=True)))


def main(ruta):
    malo = 0
    for f in sorted(pathlib.Path(ruta).rglob("*.xml")):
        for rec in ET.parse(f).getroot().iter("record"):
            if (
                rec.get("model") != "ir.ui.view"
                or rec.find("field[@name='inherit_id']") is None
            ):
                continue
            arch = rec.find("field[@name='arch']")
            if arch is None:
                continue
            for nodo in arch.iter():
                if nodo.get("position") is None:
                    continue
                if nodo.tag == "xpath":
                    m = EN_XPATH.search(nodo.get("expr", ""))
                    if m:
                        print(f"{f}: xpath por @{m.group(1)}: {nodo.get('expr')}")
                        malo = 1
                    continue
                for attr in TRADUCIBLES & set(nodo.attrib):
                    print(f"{f}: <{nodo.tag}> busca por {attr}=«{nodo.get(attr)}»")
                    malo = 1
    return malo


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
