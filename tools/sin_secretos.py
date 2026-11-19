#!/usr/bin/env python3
"""Impide subir a un repositorio PUBLICO algo que no deberia salir.

No es paranoia: este repositorio lo lee cualquiera, y un descuido en uno
publico NO se arregla borrando el commit. Para cuando alguien lo ve, ya esta
copiado e indexado, y lo unico que queda es rotar la clave -- que deja su
propia ventana en la que el portal sigue mandando la vieja.

Asi que esto corre ANTES de cada push y no despues.
"""

import re
import subprocess
import sys

# Lo que no puede salir. Cada patron lleva por que esta, porque dentro de un
# ano nadie se va a acordar y alguien lo quitara por ruidoso.
PROHIBIDO = [
    # La direccion del portal: decision de David. Si va en el codigo, un
    # repositorio publico la publica, y el modulo deja de ser inutil para
    # quien se lo lleve.
    (re.compile(r"https?://[\w.-]*(gtsagent|alma)[\w.-]*\.(ai|com|es|net)", re.I),
     "la direccion del portal va en un campo de configuracion, no en el codigo"),
    # Claves y secretos sueltos.
    (re.compile(r"\b[0-9a-f]{64}\b"),
     "parece una clave o un secreto de 64 caracteres"),
    (re.compile(r"(secret|token|api[_-]?key|password|passwd)\s*[:=]\s*['\"][^'\"]{12,}", re.I),
     "parece un secreto escrito a mano"),
    (re.compile(r"\bsk-[A-Za-z0-9]{20,}|\bghp_[A-Za-z0-9]{20,}|\bEAA[A-Za-z0-9]{40,}"),
     "parece una clave de un proveedor (OpenAI, GitHub o Meta)"),
    # Datos de clientes en ficheros de prueba.
    (re.compile(r"\+?34[\s-]?[6-9]\d{2}[\s-]?\d{2}[\s-]?\d{2}[\s-]?\d{2}"),
     "parece un telefono espanol real; en las pruebas van numeros inventados"),
]

# Lo que no se mira: ficheros binarios y el propio comprobador, que contiene
# los patrones y se denunciaria a si mismo.
SE_SALTAN = re.compile(r"\.(png|jpg|jpeg|gif|zip|pdf|po|pot)$|tools/sin_secretos\.py$")


def ficheros():
    salida = subprocess.run(
        ["git", "ls-files"], capture_output=True, text=True, check=True
    )
    return [f for f in salida.stdout.splitlines() if f and not SE_SALTAN.search(f)]


def main():
    fallos = []
    for fichero in ficheros():
        try:
            with open(fichero, encoding="utf-8") as f:
                for numero, linea in enumerate(f, 1):
                    for patron, motivo in PROHIBIDO:
                        if patron.search(linea):
                            fallos.append((fichero, numero, motivo, linea.strip()[:70]))
        except (UnicodeDecodeError, FileNotFoundError):
            continue

    if not fallos:
        print("  sin secretos                    ok")
        return 0

    print("\n  NO SUBAS ESTO. El repositorio es publico.\n")
    for fichero, numero, motivo, linea in fallos:
        print(f"  {fichero}:{numero}")
        print(f"     {motivo}")
        print(f"     > {linea}\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
