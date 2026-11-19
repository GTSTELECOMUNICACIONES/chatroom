#!/usr/bin/env python3
"""Un `message_post` sin `message_type` que luego no sale en el historial.

POR QUE EXISTE. `message_post` crea el mensaje como **«notification»** por
defecto (`mail_thread.py:2200`). Si el modulo tiene un historial que filtra
por tipo -comment, email, email_outgoing-, **todo lo que se vuelque por
codigo se guarda bien y DESAPARECE de la vista, sin un solo error**.

Me ha pasado DOS VECES, en dos verticales distintos, **teniendolo escrito en
mi memoria con la causa y la solucion exactas**. La leccion escrita no sirve;
lo que sirve es que lo compruebe algo que corre solo.

QUE MIRA. Solo si el modulo filtra por `message_type` en algun sitio: ahi, un
`message_post(` sin `message_type=` explicito es sospechoso. Si el modulo no
tiene historial, se calla: hay mil `message_post` legitimos que quieren ser
una notificacion.
"""

import pathlib
import re
import sys

LLAMADA = re.compile(r"\.message_post\s*\(", re.M)
FILTRA = re.compile(r"message_type[\"']?\s*(?:,|\]|in|==)")


def main(ruta):
    raiz = pathlib.Path(ruta)
    fuentes = sorted(raiz.rglob("*.py"))
    # ¿este módulo filtra por tipo de mensaje en alguna parte?
    filtra = any(
        FILTRA.search(f.read_text(encoding="utf-8", errors="replace")) for f in fuentes
    )
    if not filtra:
        return 0
    fallos = 0
    for f in fuentes:
        texto = f.read_text(encoding="utf-8", errors="replace")
        for casa in LLAMADA.finditer(texto):
            # el trozo de la llamada: hasta el parentesis que la cierra
            resto, nivel, fin = texto[casa.end():], 1, 0
            for i, c in enumerate(resto):
                if c == "(":
                    nivel += 1
                elif c == ")":
                    nivel -= 1
                    if nivel == 0:
                        fin = i
                        break
            argumentos = resto[:fin]
            if "message_type" in argumentos:
                continue
            linea = texto[: casa.start()].count("\n") + 1
            print(
                f"{f.name}:{linea}: `message_post` sin `message_type`. Se creará "
                "como «notification» y el historial, que filtra por tipo, NO lo "
                "enseñará. Pasa `message_type=\"comment\"`."
            )
            fallos += 1
    return 1 if fallos else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "."))
