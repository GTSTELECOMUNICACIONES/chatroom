#!/usr/bin/env python3
"""Comprueba que ningun modulo declare dependencias que su instancia no tendra.

La regla que mas caro sale olvidar: un modulo de 18.0-community/ NO puede depender
de un modulo Enterprise, porque en las instancias de IaOdoo Pymes no existe y el
modulo no instala. Ningun linter comprueba esto: es regla nuestra.

Contrasta cada 'depends' contra el fuente de la version que toca:
    18.0-community/  ->  ~/ref/odoo-18.0/addons
    19.0-enterprise/ ->  ~/ref/odoo/addons        (mas los Enterprise conocidos)

Uso:  python3 tools/check_depends.py [carpeta ...]
Sale con codigo 1 si encuentra un error.
"""

import ast
import os
import sys

HOME = os.path.expanduser("~")

# Cada carpeta del repo, con el fuente Community de SU version. Odoo tiene DOS
# rutas de addons: addons/ y odoo/addons/ (ahi vive 'base'). Hay que mirar las dos.
PLATAFORMAS = {
    "18.0-community": os.path.join(HOME, "ref/odoo-18.0"),
    "19.0-enterprise": os.path.join(HOME, "ref/odoo"),
}

# Enterprise conocidos. No se pueden enumerar del fuente: el repo odoo/odoo es
# Community, y el de Enterprise es privado. Ampliar segun aparezcan.
ENTERPRISE = {
    "account_accountant", "account_consolidation", "appointment", "approvals",
    "documents", "helpdesk", "industry_fsm", "knowledge", "marketing_automation",
    "planning", "quality", "quality_control", "sale_subscription", "sign",
    "social", "studio", "timesheet_grid", "voip", "web_studio", "whatsapp",
}


def leer_depends(manifest):
    try:
        datos = ast.literal_eval(open(manifest, encoding="utf-8").read())
    except (SyntaxError, ValueError) as exc:
        return None, f"manifiesto ilegible: {exc}"
    if not isinstance(datos, dict):
        return None, "el manifiesto no es un diccionario"
    return datos.get("depends", []), None


def revisar(carpeta):
    raiz = PLATAFORMAS.get(carpeta)
    if raiz is None:
        print(f"[SALTADA] {carpeta}: no es una plataforma conocida")
        return 0
    rutas = [os.path.join(raiz, "addons"), os.path.join(raiz, "odoo", "addons")]
    if not all(os.path.isdir(r) for r in rutas):
        print(f"[ERROR] falta el fuente de referencia en {raiz}")
        return 1
    disponibles = {m for r in rutas for m in os.listdir(r)}
    if carpeta == "19.0-enterprise":
        disponibles |= ENTERPRISE

    fallos = 0
    if not os.path.isdir(carpeta):
        return 0
    for modulo in sorted(os.listdir(carpeta)):
        manifest = os.path.join(carpeta, modulo, "__manifest__.py")
        if not os.path.isfile(manifest):
            continue
        propios = set(os.listdir(carpeta))
        depends, error = leer_depends(manifest)
        if error:
            print(f"[ERROR] {manifest}: {error}")
            fallos += 1
            continue
        for dep in depends:
            if dep in disponibles or dep in propios:
                continue
            if dep in ENTERPRISE:
                print(
                    f"[ERROR] {carpeta}/{modulo}: depende de '{dep}', que es "
                    f"Enterprise. En una instancia de Pymes no instalara."
                )
                fallos += 1
            else:
                print(
                    f"[AVISO] {carpeta}/{modulo}: '{dep}' no esta en el core de "
                    f"esta version. Si es de la OCA, bien; si no, revisar."
                )
    return fallos


def main():
    carpetas = sys.argv[1:] or list(PLATAFORMAS)
    fallos = sum(revisar(c) for c in carpetas)
    if fallos:
        print(f"\n{fallos} problema(s) de dependencias.")
        return 1
    print("Dependencias correctas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
