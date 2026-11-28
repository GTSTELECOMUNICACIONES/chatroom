#!/usr/bin/env python3
"""API de la OTRA rama, antes de que tumbe una instancia.

Modelo: gts-addons-18/tools/comprobar_api_de_rama.py (el 24-09-2026 un
`models.Constraint` copiado de la 19 dejo la demo de la 18 sin arrancar). Aqui
con lo que salio al portar el ChatRoom de la 19 a la 18 (28-11-2026), cada
entrada verificada contra ~/ref/odoo-18.0, ~/ref/odoo y la OCA de cada rama.

A diferencia del modelo, en Python solo se quitan los COMENTARIOS y los
docstrings, no todas las cadenas: aqui varios fallos viven dentro de una
cadena (`"group_ids": [...]` al crear una accion, `widget="field_selector"`).

Uso:  python3 tools/comprobar_api_de_rama.py [18.0|19.0]   (por defecto, la
rama de git). Sale con 1 si encuentra algo. Linea a proposito: «rama-ok».
"""

import ast
import io
import os
import re
import subprocess
import sys
import tokenize

SOLO_EN_19 = [
    (r"\bmodels\.Constraint\b", "en la 18 es la lista _sql_constraints"),
    (r"\bfields\.Domain\b|from odoo\.fields import Domain", "en la 18, odoo.osv.expression"),
    (r"[\"']group_ids[\"']", "en ir.actions/ir.ui.menu/res.users de la 18 es groups_id"),
    (r"\ball_group_ids\b", "en la 18 no existe (groups_id)"),
    (r"\bprivilege_id\b|res\.groups\.privilege", "en la 18 res.groups tiene category_id"),
    (r"type=[\"']jsonrpc[\"']", "en la 18 las rutas JSON son type='json'"),
    (r"\badd_model_values\b|\badd_global_values\b", "en la 18, store.add(registro, valores)"),
    (r"Store\(bus_channel=|\.bus_send\(\)", "en la 18, registro._bus_send_store(...)"),
    (r"\b_to_store_defaults\b|Store\.(One|Many|Attr)\b", "en la 18, _to_store(store) y Store.one/many"),
    (r"_to_store\(self, store[^)]*fields", "en la 18 _to_store(self, store) no lleva fields"),
    (r"\b_get_gateway_partner\b|\b_get_gateway_guest\b|\b_gateway_info\b",
     "solo en mail_gateway 19 (OCA/mail); en la 18 no existe"),
    (r"from datetime import[^\n]*\bUTC\b|datetime\.UTC\b", "Python 3.11; la 18 admite 3.10: timezone.utc"),
    # JS y plantillas OWL
    (r"\bfields\.(One|Many|Attr)\(", "en la 18, Record.one/Record.many/Record.attr"),
    (r"import\s*\{[^}]*\bfields\b[^}]*\}\s*from\s*\"@mail/core/common/record\"",
     "en la 18 se importa Record"),
    (r"@mail/discuss/core/public_web/discuss_app_category_model",
     "en la 18 esta en @mail/core/public_web/discuss_app_category_model"),
    (r"@name=.titleText.", "la plantilla del apartado de la 18 no tiene titleText"),
    (r"widget=[\"']field_selector[\"']", "en la 18 no existe: gts_field_selector"),
    (r"\.lastActiveId\b", "discuss.lastActiveId no existe en la 18"),
]

SOLO_EN_18 = [
    (r"\b_sql_constraints\b", "en la 19 es models.Constraint"),
    (r"[\"']groups_id[\"']", "en la 19 es group_ids"),
    (r"type=[\"']json[\"']", "en la 19 las rutas JSON son type='jsonrpc'"),
    (r"\b_bus_send_store\b", "en la 19, Store(bus_channel=...).bus_send()"),
    (r"\bRecord\.(one|many|attr)\(", "en la 19, fields.One/Many/Attr"),
    (r"@mail/core/public_web/discuss_app_category_model", "en la 19 esta en discuss/core"),
    (r"\bmobile\b", "en la 19 res.partner ya no tiene mobile"),
]

PROHIBIDO = {"18.0": SOLO_EN_19, "19.0": SOLO_EN_18}
MARCA = "rama-ok"
RE_XML = re.compile(r"<!--.*?-->", re.S)
RE_JS_BLOQUE = re.compile(r"/\*.*?\*/", re.S)
RE_JS_LINEA = re.compile(r"(^|[^:\"'])//[^\n]*")


def rama_del_repo():
    try:
        return subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return ""


def _en_blanco(texto):
    return re.sub(r"[^\n]", " ", texto)


def limpio(ruta):
    """El fichero sin comentarios (ni docstrings), conservando las lineas."""
    with open(ruta, encoding="utf-8") as f:
        fuente = f.read()
    if ruta.endswith(".xml"):
        return RE_XML.sub(lambda m: _en_blanco(m.group(0)), fuente)
    if ruta.endswith(".js"):
        fuente = RE_JS_BLOQUE.sub(lambda m: _en_blanco(m.group(0)), fuente)
        return RE_JS_LINEA.sub(lambda m: m.group(1), fuente)
    docstrings = set()
    try:
        for nodo in ast.walk(ast.parse(fuente)):
            cuerpo = getattr(nodo, "body", None)
            if isinstance(cuerpo, list) and cuerpo:
                primero = cuerpo[0]
                if isinstance(primero, ast.Expr) and isinstance(
                    getattr(primero, "value", None), ast.Constant
                ) and isinstance(primero.value.value, str):
                    docstrings.add(primero.lineno)
    except SyntaxError:
        return fuente
    lineas = fuente.splitlines(keepends=True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(fuente).readline):
            fila, col = tok.start
            if tok.type == tokenize.COMMENT or (
                tok.type == tokenize.STRING and fila in docstrings
            ):
                (fin_fila, fin_col) = tok.end
                for n in range(fila, fin_fila + 1):
                    linea = lineas[n - 1]
                    a = col if n == fila else 0
                    b = fin_col if n == fin_fila else len(linea.rstrip("\n"))
                    lineas[n - 1] = linea[:a] + " " * (b - a) + linea[b:]
    except (tokenize.TokenError, IndentationError):
        return fuente
    return "".join(lineas)


def revisar(raiz, rama):
    prohibido = PROHIBIDO.get(rama)
    if prohibido is None:
        sys.exit(f"No se de que plataforma es la rama «{rama}» (18.0 o 19.0).")
    fallos = []
    for carpeta, _subs, ficheros in os.walk(raiz):
        if any(p in carpeta for p in (".git", "__pycache__", "/tools", "/i18n")):
            continue
        for nombre in ficheros:
            if not nombre.endswith((".py", ".xml", ".js")):
                continue
            ruta = os.path.join(carpeta, nombre)
            with open(ruta, encoding="utf-8") as f:
                crudas = f.read().splitlines()
            for n, linea in enumerate(limpio(ruta).splitlines(), 1):
                if MARCA in (crudas[n - 1] if n <= len(crudas) else ""):
                    continue
                for patron, arreglo in prohibido:
                    if re.search(patron, linea):
                        fallos.append((os.path.relpath(ruta, raiz), n, linea.strip(), arreglo))
    return fallos


def main():
    rama = sys.argv[1] if len(sys.argv) > 1 else rama_del_repo()
    raiz = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    fallos = revisar(raiz, rama)
    if not fallos:
        print(f"Rama {rama}: ninguna API de la otra rama.")
        return 0
    print(f"Rama {rama}: {len(fallos)} usos de API que aqui NO existe.\n")
    for ruta, n, texto, arreglo in fallos:
        print(f"  {ruta}:{n}\n      {texto[:100]}\n      -> {arreglo}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
