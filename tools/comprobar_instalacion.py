#!/usr/bin/env python3
"""Caza lo que rompe la INSTALACION de un modulo y los linters no ven.

POR QUE EXISTE. En una sola semana se han caido tres instalaciones con 10/10
en pylint, black y ruff:

  1. Un `xpath` con eje descendiente que casaba con un nodo de fuera.
  2. Un proveedor sin `default_url`: muk_ai calculaba «ninguna region valida»,
     le ponia «custom» por descarte y su propia restriccion lo rechazaba.
  3. Un `groups_id` en el <record> de una vista heredada, que Odoo 18 prohibe.

Las tres tienen lo mismo en comun: **los linters leen el codigo, pero no
ejecutan la carga de datos**. Y ahi es donde estan estos fallos.

Esto no sustituye a instalar. Lo que hace es quitar de en medio los fallos que
SI se pueden ver sin levantar Odoo, que resultan ser la mayoria.

USO:
    python3 tools/comprobar_instalacion.py gts_alma_creditos
    python3 tools/comprobar_instalacion.py gts_alma_creditos --instancia

Sin `--instancia` comprueba lo que se puede leer de los ficheros. Con
`--instancia` ademas resuelve los xmlid y prueba los xpath contra una Odoo
viva, que es lo unico que los valida de verdad.

Sale con 1 si encuentra algo que impide instalar.
"""

import argparse
import ast
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from lxml import etree

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FUENTE_ODOO = os.path.expanduser("~/ref/odoo-18.0")

# Sintaxis retirada. Odoo 18 lanza ValidationError al cargar la vista
# (ir_ui_view.py:387 para attrs/states).
RETIRADO = [
    (r"\battrs\s*=", "el atributo «attrs» se retiró en la 17.0"),
    (r"\bstates\s*=", "el atributo «states» se retiró en la 17.0"),
    (r"<tree[\s>]", "«<tree>» se llama «<list>» desde la 17.0"),
    (r"</tree>", "«</tree>» se llama «</list>» desde la 17.0"),
]


class Hallazgo:
    """Un hallazgo. `grave` separa lo que impide instalar de lo que hay que
    mirar pero no para nada.

    La distincion no es cosmetica: si la herramienta dice «no subir esto» por
    un aviso menor, a la tercera vez nadie la mira. Y entonces tampoco avisa
    de lo que si rompe.
    """

    def __init__(self, fichero, linea, que, porque, grave=True):
        self.fichero, self.linea, self.que, self.porque = fichero, linea, que, porque
        self.grave = grave

    def __str__(self):
        sitio = f"{self.fichero}:{self.linea}" if self.linea else self.fichero
        return f"  {sitio}\n      {self.que}\n      -> {self.porque}\n"


# ---------------------------------------------------------------------------
# Lo que se puede ver sin instancia
# ---------------------------------------------------------------------------


def xml_del_modulo(modulo):
    for carpeta, _subs, ficheros in os.walk(os.path.join(RAIZ, modulo)):
        if "__pycache__" in carpeta:
            continue
        for nombre in sorted(ficheros):
            if nombre.endswith(".xml"):
                yield os.path.join(carpeta, nombre)


def comprobar_grupos_en_heredadas(modulo):
    """Una vista heredada NO puede llevar `groups_id` en el <record>.

    Odoo 18 lo rechaza en `_check_groups` (ir_ui_view.py:430): con inherit_id,
    modo extension y groups_id a la vez, lanza ValidationError y la
    instalacion se para. El grupo va DENTRO del arch, en el elemento.
    """
    fallos = []
    for ruta in xml_del_modulo(modulo):
        try:
            arbol = etree.parse(ruta)
        except etree.XMLSyntaxError as error:
            fallos.append(
                Hallazgo(rel(ruta), error.lineno, "XML mal formado", str(error))
            )
            continue
        for registro in arbol.iter("record"):
            if registro.get("model") != "ir.ui.view":
                continue
            campos = {c.get("name") for c in registro.findall("field")}
            if "inherit_id" not in campos or "groups_id" not in campos:
                continue
            modo = registro.find("field[@name='mode']")
            if modo is not None and (modo.text or "").strip() == "primary":
                continue
            fallos.append(
                Hallazgo(
                    rel(ruta),
                    registro.sourceline,
                    f"la vista «{registro.get('id')}» hereda y lleva groups_id",
                    "Odoo lo rechaza al cargar. El grupo va dentro del arch, "
                    'en el elemento: <page ... groups="modulo.grupo">',
                )
            )
    return fallos


def comprobar_function_en_noupdate(modulo):
    """Un <function> dentro de un fichero con noupdate NO se ejecuta al actualizar.

    Odoo lo salta expresamente (`convert.py`, `_tag_function`):

        if self.noupdate and self.mode != 'init':
            return

    Y no avisa de nada. El fichero esta bien escrito, el metodo existe, los
    linters no ven nada — simplemente no corre, y lo que tuviera que dejar
    preparado se queda sin hacer. En una instalacion NUEVA si corre, asi que
    ni siquiera falla igual en todas partes.

    Ya paso: las rutas a cada LLM se quedaban vacias al actualizar, y el chat
    contestaba «no tiene a que LLM ir» sin que se entendiera por que.
    """
    fallos = []
    for ruta in xml_del_modulo(modulo):
        try:
            arbol = etree.parse(ruta)
        except etree.XMLSyntaxError:
            continue
        raiz = arbol.getroot()
        if raiz.get("noupdate") not in ("1", "true", "True"):
            continue
        for funcion in arbol.iter("function"):
            fallos.append(
                Hallazgo(
                    rel(ruta),
                    funcion.sourceline,
                    f"<function {funcion.get('name')}> dentro de un fichero "
                    "con noupdate",
                    "Odoo lo SALTA al actualizar y no avisa. Sácalo a un "
                    "fichero sin noupdate, o no se ejecutará nunca salvo en "
                    "instalaciones nuevas",
                )
            )
    return fallos


def comprobar_sintaxis_retirada(modulo):
    fallos = []
    for ruta in xml_del_modulo(modulo):
        with open(ruta, encoding="utf-8") as fichero:
            for numero, linea in enumerate(fichero, 1):
                if linea.lstrip().startswith("<!--"):
                    continue
                for patron, porque in RETIRADO:
                    if re.search(patron, linea):
                        fallos.append(
                            Hallazgo(rel(ruta), numero, linea.strip()[:90], porque)
                        )
    return fallos


def comprobar_manifest(modulo):
    """Los ficheros del manifest tienen que existir, y la seguridad va antes."""
    fallos = []
    ruta = os.path.join(RAIZ, modulo, "__manifest__.py")
    with open(ruta, encoding="utf-8") as fichero:
        manifest = ast.literal_eval(fichero.read())

    datos = manifest.get("data", [])
    for relativo in datos:
        if not os.path.exists(os.path.join(RAIZ, modulo, relativo)):
            fallos.append(
                Hallazgo(
                    f"{modulo}/__manifest__.py",
                    None,
                    f"«{relativo}» está en data y no existe",
                    "la instalación se para en el primer fichero que falta",
                )
            )

    primero_datos = next(
        (i for i, f in enumerate(datos) if not f.startswith("security/")), None
    )
    ultimo_seg = max(
        (i for i, f in enumerate(datos) if f.startswith("security/")), default=None
    )
    if (
        primero_datos is not None
        and ultimo_seg is not None
        and ultimo_seg > primero_datos
    ):
        fallos.append(
            Hallazgo(
                f"{modulo}/__manifest__.py",
                None,
                "la seguridad se carga después de los datos",
                "las ACL tienen que existir antes de crear registros que las usen",
            )
        )
    return fallos


# ---------------------------------------------------------------------------
# Lo que solo se puede ver contra una instancia
# ---------------------------------------------------------------------------


def xmlids_propios(modulo):
    propios = set()
    for ruta in xml_del_modulo(modulo):
        try:
            arbol = etree.parse(ruta)
        except etree.XMLSyntaxError:
            continue
        for nodo in arbol.iter():
            if nodo.get("id"):
                propios.add(f"{modulo}.{nodo.get('id')}")
                propios.add(nodo.get("id"))
    csv = os.path.join(RAIZ, modulo, "security", "ir.model.access.csv")
    if os.path.exists(csv):
        with open(csv, encoding="utf-8") as fichero:
            for linea in list(fichero)[1:]:
                if linea.strip():
                    propios.add(f"{modulo}.{linea.split(',')[0]}")
    return propios


def xmlids_referenciados(modulo):
    """Todo `ref="x.y"` y `ref('x.y')` que aparece en los XML."""
    vistos = {}
    for ruta in xml_del_modulo(modulo):
        with open(ruta, encoding="utf-8") as fichero:
            for numero, linea in enumerate(fichero, 1):
                if linea.lstrip().startswith("<!--"):
                    continue
                for xmlid in re.findall(
                    r'ref=["\']([a-zA-Z0-9_]+\.[a-zA-Z0-9_]+)["\']', linea
                ):
                    vistos.setdefault(xmlid, (rel(ruta), numero))
                for xmlid in re.findall(r"ref\(['\"]([a-zA-Z0-9_.]+)['\"]\)", linea):
                    vistos.setdefault(xmlid, (rel(ruta), numero))
    return vistos


def normalizar(xmlid, modulo):
    """Un `ref` sin punto es del propio modulo."""
    return xmlid if "." in xmlid else f"{modulo}.{xmlid}"


class FalloDeConsulta(Exception):
    """No se ha podido preguntar. NO es lo mismo que «no existe»."""


def mcp(herramienta, argumentos, intentos=3):
    """Pregunta a la instancia. Lanza si no se puede preguntar.

    LO IMPORTANTE ES QUE LANCE. La primera version devolvia None cuando algo
    fallaba y quien llamaba hacia `or []`, con lo que un fallo de red se
    convertia en «ese xmlid no existe». Y eso salia impreso como un error del
    modulo: mandaba a buscar un fallo que no estaba.

    Es el mismo error que esta herramienta viene a evitar, cometido aqui
    dentro. «No he podido comprobarlo» y «esta mal» son dos cosas distintas y
    hay que pintarlas distinto.

    Se reintenta porque el fallo suele ser pasajero: cada llamada abre su
    propia sesion MCP y con decenas seguidas alguna se cae.
    """
    guion = os.environ.get("GTS_MCP_DEMO", "")
    if not guion or not os.path.exists(guion):
        sys.exit(
            "Para --instancia hace falta el cliente MCP en GTS_MCP_DEMO.\n"
            "Sin el, el modo sin instancia funciona igual."
        )
    ultimo = ""
    for _ in range(intentos):
        salida = subprocess.run(
            [guion, herramienta, json.dumps(argumentos)],
            capture_output=True,
            text=True,
            check=False,
        )
        try:
            crudo = json.loads(salida.stdout)["result"]["content"][0]["text"]
            return json.loads(crudo)
        except Exception as error:
            ultimo = f"{type(error).__name__}: {str(error)[:80]}"
    raise FalloDeConsulta(ultimo or "sin respuesta")


def comprobar_xmlids(modulo):
    """Cada xmlid ajeno tiene que existir en la instancia.

    Es el fallo mas silencioso de todos: un `ref` a algo que no esta para la
    instalacion en seco, sin decir por que. Y pasa con facilidad —
    `base.model_report_layout` y no `web.`, `ir.actions.client` y no
    `act_window`— porque el nombre PARECE correcto.
    """
    fallos = []
    propios = xmlids_propios(modulo)
    referencias = {
        k: v for k, v in xmlids_referenciados(modulo).items() if k not in propios
    }
    if not referencias:
        return fallos

    por_modulo = {}
    for xmlid in referencias:
        mod, _, nombre = xmlid.partition(".")
        por_modulo.setdefault(mod, []).append(nombre)

    existen = set()
    for mod, nombres in por_modulo.items():
        datos = (
            mcp(
                "search_read",
                {
                    "model": "ir.model.data",
                    "domain": json.dumps(
                        [["module", "=", mod], ["name", "in", nombres]]
                    ),
                    "fields": ["module", "name"],
                    "limit": 500,
                },
            )
            or []
        )
        existen |= {f"{r['module']}.{r['name']}" for r in datos}

    for xmlid, (fichero, numero) in sorted(referencias.items()):
        if xmlid not in existen:
            fallos.append(
                Hallazgo(
                    fichero,
                    numero,
                    f"«{xmlid}» no existe en la instancia",
                    "la instalación se para. Comprobar el módulo: un modelo "
                    "lleva el de donde se DEFINE, no el de donde se usa",
                )
            )
    return fallos


def comprobar_xpath(modulo):
    """Cada xpath tiene que casar con algo en la vista que hereda.

    Un xpath que no casa **para la instalacion**. Y uno que casa con VARIOS es
    peor: instala, aplica el primero, y el cambio aparece donde no toca meses
    despues. Eso ya nos rompio el formulario de proyecto de la demo.

    Se compara contra el arch COMBINADO —el que devuelve `get_view`— y no
    contra el original, porque una vista nuestra puede hacer xpath sobre un
    nodo que anadio otra vista nuestra anterior.
    """
    fallos = []
    try:
        _xpath_interno(modulo, fallos)
    except FalloDeConsulta as error:
        fallos.append(
            Hallazgo(
                modulo,
                None,
                "no se han podido comprobar los xpath",
                f"la instancia no respondio ({error}). No quiere decir que "
                "esten mal: quiere decir que no se sabe. Repetir.",
                grave=False,
            )
        )
    return fallos


def _arch_combinado(res_id):
    """Devuelve el arch de una vista TAL COMO Odoo la monta.

    Con `get_view`, que es el metodo publico que aplica las herencias en
    cadena. Hace falta el combinado y no el original porque una vista nuestra
    puede hacer xpath sobre un nodo que anadio otra vista nuestra anterior:
    comparando contra el original salen falsos positivos, y un comprobador
    que se equivoca se deja de mirar en dos dias.

    (El privado `_get_combined_arch` no se puede llamar en remoto, y Odoo hace
    bien en no dejarlo.)
    """
    ficha = mcp(
        "read_records",
        {
            "model": "ir.ui.view",
            "ids": [res_id],
            "fields": ["model", "type", "arch_db"],
        },
    )
    if not ficha:
        return None
    if ficha[0].get("model"):
        try:
            combinado = mcp(
                "call_method",
                {
                    "model": ficha[0]["model"],
                    "method": "get_view",
                    "kwargs": {
                        "view_id": res_id,
                        "view_type": ficha[0].get("type") or "form",
                    },
                },
            )
        except FalloDeConsulta:
            combinado = None
        if isinstance(combinado, dict) and combinado.get("arch"):
            return combinado["arch"]
    return ficha[0].get("arch_db")


def _xpath_interno(modulo, fallos):
    """Prueba cada xpath del modulo contra la vista que hereda, de verdad."""
    cache_arch = {}
    for ruta in xml_del_modulo(modulo):
        try:
            arbol = etree.parse(ruta)
        except etree.XMLSyntaxError:
            continue
        for registro in arbol.iter("record"):
            if registro.get("model") != "ir.ui.view":
                continue
            heredada = registro.find("field[@name='inherit_id']")
            if heredada is None or not heredada.get("ref"):
                continue
            xpaths = [x for x in registro.iter("xpath") if x.get("expr")]
            if not xpaths:
                continue

            # Un `ref` sin punto es del propio modulo. Sin esto, `partition`
            # deja el nombre vacio y la consulta no encuentra nada NUNCA: el
            # comprobador denunciaria como inexistente un xmlid que si esta.
            referencia = normalizar(heredada.get("ref"), modulo)
            modulo_ref, _, nombre_ref = referencia.partition(".")

            if referencia not in cache_arch:
                datos = mcp(
                    "search_read",
                    {
                        "model": "ir.model.data",
                        "domain": json.dumps(
                            [["module", "=", modulo_ref], ["name", "=", nombre_ref]]
                        ),
                        "fields": ["res_id"],
                    },
                )
                cache_arch[referencia] = (
                    _arch_combinado(datos[0]["res_id"]) if datos else None
                )

            arch = cache_arch[referencia]
            if not arch:
                fallos.append(
                    Hallazgo(
                        rel(ruta),
                        heredada.sourceline,
                        f"no se ha podido leer la vista «{referencia}»",
                        "o no existe en la instancia, o no está instalado el "
                        "módulo que la define. Sus xpath quedan sin comprobar",
                        grave=False,
                    )
                )
                continue

            try:
                raiz = etree.fromstring(arch.encode())
            except etree.XMLSyntaxError:
                continue

            for xpath in xpaths:
                expr = xpath.get("expr")
                try:
                    casan = raiz.xpath(expr)
                except etree.XPathEvalError as error:
                    fallos.append(
                        Hallazgo(rel(ruta), xpath.sourceline, expr, str(error))
                    )
                    continue
                if not casan:
                    fallos.append(
                        Hallazgo(
                            rel(ruta),
                            xpath.sourceline,
                            f"el xpath «{expr}» no casa con nada",
                            f"en {referencia}. La instalación se para",
                        )
                    )
                elif len(casan) > 1:
                    fallos.append(
                        Hallazgo(
                            rel(ruta),
                            xpath.sourceline,
                            f"el xpath «{expr}» casa con {len(casan)} nodos",
                            "Odoo aplica el PRIMERO. Instala igual, pero si no "
                            "es el que quieres el cambio sale donde no toca, y "
                            "eso se descubre meses después",
                            grave=False,
                        )
                    )


def rel(ruta):
    return os.path.relpath(ruta, RAIZ)


def comprobar_orden_de_refs(modulo):
    """Un `ref()` a un xmlid definido MAS ABAJO en el mismo fichero.

    Odoo carga el XML en orden. Un `ref('x')` cuando `x` se define veinte
    lineas mas abajo lanza «External ID not found», la actualizacion entera
    falla y se deshace — y el modulo se queda en la version anterior mientras
    la ficha sigue diciendo la nueva. Media hora dando vueltas el 16-10-2026.

    No cubre los refs ENTRE ficheros: para eso manda el orden del manifest, y
    eso ya lo mira `comprobar_ficheros_del_manifest`.
    """
    fallos = []
    for ruta in sorted(Path(modulo).rglob("*.xml")):
        texto = ruta.read_text(encoding="utf-8")
        definidos = {}
        for m in re.finditer(r'<record[^>]*\bid="([^".]+)"', texto):
            definidos.setdefault(m.group(1), m.start())
        for m in re.finditer(r"ref\(\s*['\"]([^'\"]+)['\"]\s*\)", texto):
            xmlid = m.group(1)
            if "." in xmlid:
                continue
            donde = definidos.get(xmlid)
            if donde is not None and donde > m.start():
                linea = texto[: m.start()].count("\n") + 1
                linea_def = texto[:donde].count("\n") + 1
                fallos.append(
                    Hallazgo(
                        str(ruta),
                        linea,
                        f"ref('{xmlid}') apunta a un registro que se define "
                        f"mas abajo, en la linea {linea_def}",
                        "Odoo carga el XML EN ORDEN: aqui lanza «External ID "
                        "not found», la actualizacion entera falla y se "
                        "deshace. Y el modulo se queda en la version anterior "
                        "mientras su ficha sigue diciendo la nueva, que es lo "
                        "que despista. Mueve la definicion arriba.",
                    )
                )
    return fallos


def main():
    analizador = argparse.ArgumentParser(description=__doc__)
    analizador.add_argument("modulo")
    analizador.add_argument(
        "--instancia",
        action="store_true",
        help="además valida xmlid y xpath contra una Odoo viva",
    )
    opciones = analizador.parse_args()

    if not os.path.isdir(os.path.join(RAIZ, opciones.modulo)):
        sys.exit(f"No existe el módulo {opciones.modulo}.")

    print(f"\nCOMPROBANDO {opciones.modulo}\n")
    bloques = [
        ("Grupos en vistas heredadas", comprobar_grupos_en_heredadas),
        ("Sintaxis retirada", comprobar_sintaxis_retirada),
        ("<function> en fichero noupdate", comprobar_function_en_noupdate),
        ("Ficheros del manifest", comprobar_manifest),
        ("Orden de los ref() dentro de un fichero", comprobar_orden_de_refs),
    ]
    if opciones.instancia:
        bloques += [
            ("xmlid que referencia", comprobar_xmlids),
            ("xpath contra la vista real", comprobar_xpath),
        ]

    todos = []
    for titulo, funcion in bloques:
        # Una comprobacion que no puede preguntar NO tumba la herramienta ni
        # se da por buena: se dice que no se ha podido comprobar. Un traceback
        # no le sirve a nadie, y un «ok» silencioso es mentira.
        try:
            fallos = funcion(opciones.modulo)
        except FalloDeConsulta as error:
            fallos = [
                Hallazgo(
                    opciones.modulo,
                    None,
                    f"«{titulo}» no se ha podido comprobar",
                    f"la instancia no respondió ({error}). No quiere decir que "
                    "esté bien: quiere decir que no se sabe. Repetir.",
                    grave=False,
                )
            ]
        g = sum(1 for f in fallos if f.grave)
        a = len(fallos) - g
        marca = "ok" if not fallos else (f"{g} FALLO(S)" if g else f"{a} aviso(s)")
        print(f"  {marca:<12} {titulo}")
        todos += fallos

    if not opciones.instancia:
        print("\n  (sin --instancia no se comprueban los xmlid ni los xpath,")
        print("   que son los que más silenciosamente rompen la instalación)")

    graves = [h for h in todos if h.grave]
    avisos = [h for h in todos if not h.grave]

    if graves:
        print(f"\n{len(graves)} COSA(S) QUE ROMPEN LA INSTALACIÓN:\n")
        for hallazgo in graves:
            print(hallazgo)

    if avisos:
        print(f"\n{len(avisos)} COSA(S) QUE INSTALAN, PERO CONVIENE MIRAR:\n")
        for hallazgo in avisos:
            print(hallazgo)

    if graves:
        print("NO subir esto a ningún cliente hasta arreglarlo.\n")
        return 1
    if avisos:
        print("Se puede instalar. Los avisos de arriba no paran nada.\n")
        return 0
    print("\nNada que impida instalar.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
