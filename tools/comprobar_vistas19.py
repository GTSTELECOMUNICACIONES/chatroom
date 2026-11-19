#!/usr/bin/env python3
"""Las trampas de vistas de la 19 que revientan al INSTALAR.

POR QUE PASA. La vista `search` SI tiene esquema RelaxNG
(`odoo/addons/base/rng/search_view.rng`, que incluye `common.rng`) y la vista
`form` NO -no existe `form_view.rng`-. Por eso `<group string="...">` vale en
un formulario, con 262 usos en el core, y revienta en una busqueda, con cero.
No es que `expand` este prohibido: es que dentro de `<search>` solo valen los
atributos que el RNG enumera, y cualquier otro tumba la instalacion.

Los atributos que `common.rng` permite en `<group>` (lineas 295-312):
position, groups, colspan, rowspan, fill, height, width, name, color,
invisible, col. Ni `expand` ni `string`.

El error de instalacion no nombra el atributo ni apunta a su linea: senala el
<record> entero, y por eso cuesta tanto encontrarlo.

Tambien avisa de un `<group>` sin `name=` en cualquier vista: hoy no falla,
pero en la 19 no se puede heredar apuntando a un grupo por su `string`, asi
que el dia que otro modulo necesite colgarse de ahi ya es una migracion.

LOS COMENTARIOS NO CUENTAN. La primera version miraba las lineas en crudo y
se bloqueo sola con un comentario mio que mencionaba `<group>`. Un
comprobador que da un falso positivo acaba desactivado, y entonces no sirve
de nada el dia que acierte.
"""

import pathlib
import re
import sys

COMENTARIO = re.compile(r"<!--.*?-->", re.DOTALL)
ATRIBUTO = re.compile(r"([\w:-]+)\s*=")

# Lo que common.rng permite en <group>. Cualquier otro atributo rompe la
# instalacion si el <group> esta dentro de una vista search.
PERMITIDOS_EN_SEARCH = {
    "position", "groups", "colspan", "rowspan", "fill", "height", "width",
    "name", "color", "invisible", "col",
}


def sin_comentarios(texto):
    """Vacia los comentarios dejando los saltos de linea en su sitio.

    Hay que conservar las lineas para poder decir en cual esta el fallo.
    """
    return COMENTARIO.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), texto)


def main(ruta):
    fallos = 0
    for fichero in sorted(pathlib.Path(ruta).rglob("*.xml")):
        limpio = sin_comentarios(fichero.read_text())
        en_search = False
        for numero, linea in enumerate(limpio.splitlines(), 1):
            if "<search" in linea:
                en_search = True
            if "</search>" in linea:
                en_search = False
            if "<group" not in linea:
                continue
            etiqueta = linea[linea.index("<group"):]
            atributos = set(ATRIBUTO.findall(etiqueta.split(">")[0]))
            if en_search:
                sobran = sorted(atributos - PERMITIDOS_EN_SEARCH)
                if sobran:
                    print(
                        "%s:%s: <group> dentro de <search> con %s; el RNG de "
                        "search no lo permite y rompe la instalación"
                        % (fichero.name, numero, ", ".join("`%s`" % a for a in sobran))
                    )
                    fallos += 1
            elif "expand=" in etiqueta:
                print(
                    "%s:%s: `expand` no existe en la 19 y rompe la "
                    "instalación" % (fichero.name, numero)
                )
                fallos += 1
            if "name" not in atributos:
                print(
                    "%s:%s: <group> sin `name=`; no se podrá heredar"
                    % (fichero.name, numero)
                )
                fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
