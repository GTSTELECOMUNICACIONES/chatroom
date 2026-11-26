#!/usr/bin/env bash
# Todo lo que hay que pasar antes de un push.
#
# POR QUE EXISTE ESTO Y NO BASTA LA NOTA. El 16-11-2026 la instalacion en
# staging se cayo por un `<group expand="0">`, que es una trampa que yo mismo
# tenia escrita en la memoria Y para la que yo mismo habia escrito el
# comprobador. No lo pase. Tener la leccion escrita no sirve de nada; lo que
# sirve es que lo compruebe una herramienta que corre sola.
#
# Sin argumentos revisa TODOS los modulos del repo. Con argumentos, solo esos.
set -u
VENV=~/venv/bin
RAIZ="$(cd "$(dirname "$0")/.." && pwd)"

if [ "$#" -gt 0 ]; then
    MODULOS=()
    for m in "$@"; do MODULOS+=("$RAIZ/$(basename "$m")"); done
else
    mapfile -t MODULOS < <(find "$RAIZ" -mindepth 2 -maxdepth 2 -name __manifest__.py \
                           -printf '%h\n' | sort)
fi
if [ "${#MODULOS[@]}" -eq 0 ]; then echo "No hay ningun modulo en $RAIZ."; exit 1; fi

fallos=0
# Si pasa, se enseña igualmente lo que el comprobador diga que NO ha podido
# comprobar: un hueco silencioso se lee como cobertura, y no lo es.
paso() { printf '  %-32s' "$1"; shift; if "$@" >/tmp/_c.out 2>&1; then echo "ok"; grep '^   (' /tmp/_c.out || true; else echo "FALLA"; sed 's/^/     /' /tmp/_c.out | head -25; fallos=$((fallos+1)); fi; }

for MOD in "${MODULOS[@]}"; do
  if [ ! -f "$MOD/__manifest__.py" ]; then echo "$(basename "$MOD"): no es un modulo."; fallos=$((fallos+1)); continue; fi
  echo "== $(basename "$MOD")"

  paso "ruff"      $VENV/ruff check "$MOD"
  paso "black"     $VENV/black --check -q "$MOD"
  paso "XML"       $VENV/python - "$MOD" <<'PY'
import sys, pathlib, xml.dom.minidom
for f in pathlib.Path(sys.argv[1]).rglob("*.xml"):
    xml.dom.minidom.parse(str(f))
PY
  # Las trampas de vistas de la 19: dentro de <search> solo valen los
  # atributos del RNG, y todo <group> necesita `name` para poder heredarse.
  paso "trampas de vistas de la 19" $VENV/python "$RAIZ/tools/comprobar_vistas19.py" "$MOD"

  paso "campos de las vistas y ACL" $VENV/python "$RAIZ/tools/comprobar_campos.py" "$MOD"
  paso "selectores traducibles" $VENV/python "$RAIZ/tools/comprobar_selectores.py" "$MOD"
  paso "anclajes de vistas heredadas" $VENV/python "$RAIZ/tools/comprobar_herencias.py" "$MOD"
  paso "xmlid de otros modulos" $VENV/python "$RAIZ/tools/comprobar_xmlids.py" "$MOD"
  paso "dominios sobre calculados" $VENV/python "$RAIZ/tools/comprobar_dominios.py" "$MOD"
  paso "message_post y el historial" $VENV/python "$RAIZ/tools/comprobar_mensajes.py" "$MOD"
  paso "inversos de los One2many" $VENV/python "$RAIZ/tools/comprobar_inversos.py" "$MOD"
  paso "x2many repetidos" $VENV/python "$RAIZ/tools/comprobar_duplicados.py" "$MOD"
  paso "instalacion" $VENV/python "$RAIZ/tools/comprobar_instalacion.py" "$MOD"
  paso "campos en modelos calientes" $VENV/python "$RAIZ/tools/comprobar_modelos_calientes.py" "$MOD"
  # necesita el fuente de los modulos cuyos <record> creamos
  # --fuente son FICHEROS .py, no carpetas: el fuente de los modelos cuyos
  # <record> creamos nosotros (res.groups, ir.rule...) y los nuestros.
  paso "records contra su modelo" $VENV/python "$RAIZ/tools/comprobar_records.py" \
       --fuente $HOME/ref/odoo/odoo/addons/base/models/*.py \
                $HOME/ref/odoo/addons/mail/models/*.py \
                "$MOD"/models/*.py \
       -- $(find "$MOD" -name "*.xml")
  paso "api.depends" $VENV/python "$RAIZ/tools/comprobar_api_depends.py" "$MOD"
  paso "depends del manifest" $VENV/python "$RAIZ/tools/check_depends.py" "$MOD"
done

paso "sin secretos (repo publico)" $VENV/python "$RAIZ/tools/sin_secretos.py"
echo
if [ "$fallos" -gt 0 ]; then echo "$fallos comprobaciones FALLAN. No subas esto."; exit 1; fi
echo "Todo pasa."
