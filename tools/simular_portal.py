#!/usr/bin/env python3
"""Hace de portal de Alma contra la URL de entrada de una pasarela `alma`.

Mientras el portal no mande nada (19-11-2026: del lado del portal no existe
todavia el reenvio firmado), esto es lo unico que deja probar la entrada de
punta a punta: construye eventos con la forma EXACTA del contrato del LEEME,
los firma como espera el controlador y los manda por POST.

LA FIRMA, byte a byte igual que `_verify_update` en
`gts_chatroom/models/mail_gateway_alma.py`:

    X-Alma-Ts:    <segundos unix>
    X-Alma-Firma: sha256=<hex de HMAC-SHA256(secreto, b"<ts>." + cuerpo crudo)>

El cuerpo se serializa UNA vez y se firman y se mandan ESOS bytes. Si se
firmara un JSON y se mandara otro (otro orden de claves, otros espacios), la
firma no cuadraria y Odoo lo tiraria en silencio.

OJO: el controlador de la OCA contesta `200 {}` TAMBIEN cuando rechaza la
firma o no encuentra la pasarela. Que el envio diga 200 no prueba que haya
entrado: mirar la conversacion en Odoo o el log («could not be verified»).

EL SECRETO Y LA URL NO VAN NUNCA EN ESTE FICHERO: el repositorio es publico.
Se pasan por argumento o, mejor, por variable de entorno (un argumento se ve
en la lista de procesos):

    ALMA_URL_ENTRADA   la «URL de entrada» de la ficha de la pasarela
    ALMA_SECRETO       el «secreto» (webhook_secret) de la pasarela
    ALMA_NUMERO        el «Numero de WhatsApp» de la pasarela

Ejemplos (valores inventados):

    export ALMA_URL_ENTRADA=https://staging.ejemplo.com/gateway/alma/CLAVE/update
    export ALMA_SECRETO=...            # copiado de la ficha, no escrito aqui
    export ALMA_NUMERO=15555550100
    python3 tools/simular_portal.py cliente --texto "Hola, ¿abrís el sábado?"
    python3 tools/simular_portal.py escala
    python3 tools/simular_portal.py acuse --mensaje-id m-123 --estado leido
    python3 tools/simular_portal.py recorrido --solo-imprimir

Los telefonos por defecto son del rango 555-01xx de Norteamerica, reservado
para ficcion. No pongas aqui telefonos reales: `tools/sin_secretos.py` lo
impide, y con razon.
"""

import argparse
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime, timedelta

TELEFONO_FICTICIO = "15555550123"
NOMBRE_FICTICIO = "Cliente de prueba"


# ----------------------------------------------------------------------
# La forma del evento (contrato 0.2 del LEEME)
# ----------------------------------------------------------------------


def _iso(momento):
    return momento.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def evento(args, tipo, emitido_en=None, mensaje=None, acuse=None, estado=None):
    """Un evento con TODAS las piezas del contrato. Lo que no aplica va a
    null, no se omite: asi se prueba tambien que Odoo lo tolera."""
    return {
        "tipo": tipo,
        "emitido_en": _iso(emitido_en or datetime.now(UTC)),
        "numero": args.numero,
        "conversacion": {
            "id": args.conversacion or f"sim-{args.telefono}",
            "contacto": {"telefono": args.telefono, "nombre": args.nombre},
        },
        "mensaje": mensaje,
        "acuse": acuse,
        "estado": estado,
    }


def mensaje(de, texto, mensaje_id=None, adjuntos=None):
    return {
        "id": mensaje_id or f"sim-{uuid.uuid4().hex[:12]}",
        "de": de,
        "texto": texto,
        # La hora real del mensaje (contrato: `mensaje.cuando`).
        "cuando": _iso(datetime.now(UTC)),
        "adjuntos": adjuntos or [],
    }


def estado(relevo, asignado_a=None, espera=False, motivo=None):
    return {
        "relevo": relevo,
        "asignado_a": asignado_a,
        "espera": espera,
        "motivo_relevo": motivo,
    }


# ----------------------------------------------------------------------
# Los escenarios: cada uno devuelve la lista de eventos, en el orden en que
# se mandan.
# ----------------------------------------------------------------------


def esc_cliente(args):
    texto = args.texto or "Hola, quería información sobre vuestros servicios."
    adjuntos = []
    if args.con_adjunto:
        # Hoy el portal no guarda los ficheros: la url llega null.
        adjuntos = [{"nombre": "foto.jpg", "mime": "image/jpeg", "url": None}]
    return [
        evento(
            args,
            "mensaje",
            mensaje=mensaje("contacto", texto, args.mensaje_id, adjuntos),
            estado=estado("ia"),
        )
    ]


def esc_ia(args):
    texto = args.texto or "¡Hola! Soy Alma, la asistente. ¿En qué puedo ayudarle?"
    return [
        evento(
            args,
            "mensaje",
            mensaje=mensaje("ia", texto, args.mensaje_id),
            estado=estado("ia"),
        )
    ]


def esc_escala(args):
    """La IA contesta y DESPUES escala: el ultimo mensaje es suyo, y aun asi
    la conversacion tiene que quedar en «esperando»."""
    texto = args.texto or "Le paso con una persona del equipo, enseguida le atienden."
    return [
        evento(
            args,
            "mensaje",
            mensaje=mensaje("ia", texto, args.mensaje_id),
            estado=estado(
                "humano", espera=True, motivo="El cliente pide hablar con una persona"
            ),
        )
    ]


def esc_movil(args):
    """El dueño contesta desde su propio WhatsApp antes de que nadie la coja:
    tiene que pasar a «contestada, sin asignar», no seguir en «esperando»."""
    texto = args.texto or "Hola, soy yo, te lo miro ahora mismo."
    return [
        evento(
            args,
            "mensaje",
            mensaje=mensaje("movil", texto, args.mensaje_id),
            estado=estado("humano", espera=False),
        )
    ]


def esc_estado(args):
    """Solo el aviso de cambio de estado, sin mensaje."""
    return [
        evento(
            args,
            "estado",
            estado=estado(
                args.relevo,
                asignado_a=args.asignado_a,
                espera=args.espera,
                motivo=args.motivo,
            ),
        )
    ]


def esc_acuse(args):
    if not args.mensaje_id:
        sys.exit(
            "acuse necesita --mensaje-id: el id que el portal devolvio al "
            "enviar (en Odoo, el «gateway_message_id» de la notificacion)."
        )
    acuse = {
        "mensaje_id": args.mensaje_id,
        "estado": args.estado,
        "alma_motivo": args.alma_motivo,
        "detalle": None,
    }
    return [evento(args, "acuse", acuse=acuse)]


def esc_repetido(args):
    """El MISMO mensaje dos veces (mismo mensaje.id). En Odoo tiene que quedar
    uno solo."""
    texto = args.texto or "Este mensaje llega dos veces y debe verse una."
    fijo = args.mensaje_id or f"sim-rep-{uuid.uuid4().hex[:8]}"
    uno = evento(
        args, "mensaje", mensaje=mensaje("contacto", texto, fijo), estado=estado("ia")
    )
    return [uno, dict(uno)]


def esc_viejo(args):
    """Fuera de orden: primero llega el estado NUEVO (espera a una persona) y
    despues uno emitido diez minutos ANTES (con la IA). El viejo no puede
    pisar al nuevo: la conversacion tiene que seguir en «esperando»."""
    ahora = datetime.now(UTC)
    nuevo = evento(
        args,
        "estado",
        emitido_en=ahora,
        estado=estado("humano", espera=True, motivo="Evento nuevo"),
    )
    viejo = evento(
        args, "estado", emitido_en=ahora - timedelta(minutes=10), estado=estado("ia")
    )
    return [nuevo, viejo]


def esc_eco(args):
    """EL BUCLE DEL 21-11-2026. El portal reenvia a Odoo lo que escribe un
    operador (desde el ChatRoom del portal y, hasta que Carlos lo corto, lo
    que salia de Odoo) como `mensaje` con de=operador. Odoo tiene que
    PUBLICARLO y NO volver a mandarlo a /enviar.

    Con `--mensaje-id <gateway_message_id de un envio de Odoo>` es el eco de
    un mensaje que mando Odoo: ademas, no tiene que aparecer dos veces.

    Se comprueba en Odoo, no aqui (el controlador contesta 200 siempre): el
    mensaje sale como la empresa, con origen «Equipo, desde el ChatRoom», y
    NO tiene notificacion de pasarela (mail.notification de tipo gateway).
    Usar SIEMPRE el telefono ficticio: si el arreglo fallara, saldria un
    WhatsApp de verdad."""
    texto = args.texto or "Eco de prueba: esto no tiene que volver a salir."
    return [
        evento(
            args,
            "mensaje",
            mensaje=mensaje("operador", texto, args.mensaje_id),
            estado=estado("humano", espera=False),
        )
    ]


def esc_recorrido(args):
    """Una conversacion entera: escribe, contesta la IA, escala, se repite un
    mensaje y llega un evento viejo."""
    eventos = []
    for escenario in (esc_cliente, esc_ia, esc_escala, esc_repetido, esc_viejo):
        eventos += escenario(args)
    return eventos


ESCENARIOS = {
    "cliente": (esc_cliente, "el cliente escribe (la conversacion sigue con la IA)"),
    "ia": (esc_ia, "la IA contesta"),
    "escala": (esc_escala, "la IA contesta y escala a una persona (espera)"),
    "movil": (esc_movil, "el dueño contesta desde su movil: «contestada»"),
    "estado": (esc_estado, "solo un cambio de estado (--relevo, --espera...)"),
    "acuse": (esc_acuse, "acuse de un mensaje enviado (--mensaje-id, --estado)"),
    "repetido": (esc_repetido, "el mismo mensaje.id dos veces"),
    "eco": (esc_eco, "un operador del portal (de=operador): se publica y NO sale"),
    "viejo": (esc_viejo, "un estado viejo que llega despues de uno nuevo"),
    "recorrido": (esc_recorrido, "todo lo anterior seguido, menos acuse y movil"),
}


# ----------------------------------------------------------------------
# Firmar y mandar
# ----------------------------------------------------------------------


def cuerpo_crudo(datos):
    """Los bytes que se firman Y se mandan. Una sola serializacion."""
    return json.dumps(datos, ensure_ascii=False, separators=(",", ":")).encode()


def firmar(secreto, ts, crudo):
    hexa = hmac.new(
        secreto.encode(), str(ts).encode() + b"." + crudo, hashlib.sha256
    ).hexdigest()
    return f"sha256={hexa}"


def cabeceras(secreto, crudo, desfase=0, firma_mala=False):
    ts = int(time.time()) + desfase
    firma = firmar(secreto, ts, crudo) if secreto else "(sin secreto: no se firma)"
    if firma_mala and secreto:
        firma = firmar(secreto + "x", ts, crudo)
    return {
        "Content-Type": "application/json; charset=utf-8",
        "X-Alma-Ts": str(ts),
        "X-Alma-Firma": firma,
    }


def mandar(url, cab, crudo, timeout):
    peticion = urllib.request.Request(url, data=crudo, headers=cab, method="POST")
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as respuesta:
            return respuesta.status, respuesta.read().decode(errors="replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def imprimir(url, cab, crudo, secreto):
    print(f"POST {url or '(sin URL)'}")
    for clave, valor in cab.items():
        if clave == "X-Alma-Firma" and secreto and len(valor) > 20:
            valor = valor[:20] + "…"  # la firma no es secreta, pero es ruido
        print(f"{clave}: {valor}")
    print()
    print(json.dumps(json.loads(crudo), ensure_ascii=False, indent=2))
    print()


def argumentos(argv):
    lista = "\n".join(f"  {n:<10} {d}" for n, (_f, d) in ESCENARIOS.items())
    p = argparse.ArgumentParser(
        description="Simula el portal de Alma contra la URL de entrada de una "
        "pasarela `alma` de Odoo.",
        epilog="Escenarios:\n" + lista,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("escenario", choices=ESCENARIOS)
    p.add_argument(
        "--url",
        default=os.environ.get("ALMA_URL_ENTRADA"),
        help="URL de entrada de la pasarela (o ALMA_URL_ENTRADA)",
    )
    p.add_argument(
        "--secreto",
        default=os.environ.get("ALMA_SECRETO"),
        help="secreto de la pasarela (mejor por ALMA_SECRETO: un argumento se "
        "ve en la lista de procesos)",
    )
    p.add_argument(
        "--numero",
        default=os.environ.get("ALMA_NUMERO"),
        help="numero emisor de la pasarela, con prefijo y sin + (o ALMA_NUMERO). "
        "Si no casa con el de la pasarela, Odoo descarta el evento",
    )
    p.add_argument("--telefono", default=TELEFONO_FICTICIO, help="telefono del cliente")
    p.add_argument("--nombre", default=NOMBRE_FICTICIO, help="nombre del cliente")
    p.add_argument(
        "--conversacion", help="id de la conversacion en el portal (sim-<telefono>)"
    )
    p.add_argument("--texto", help="texto del mensaje")
    p.add_argument("--mensaje-id", help="id del mensaje (o del enviado, en acuse)")
    p.add_argument("--con-adjunto", action="store_true", help="cliente: con foto")
    p.add_argument(
        "--estado",
        default="entregado",
        choices=["en_cola", "salio", "entregado", "leido", "fallido"],
        help="acuse: estado del mensaje",
    )
    p.add_argument("--alma-motivo", help="acuse fallido: sin_saldo, bloqueado...")
    p.add_argument("--relevo", default="humano", choices=["ia", "humano", "resuelta"])
    p.add_argument("--asignado-a", help="estado: correo de quien la atiende")
    p.add_argument("--espera", action="store_true", help="estado: espera respuesta")
    p.add_argument("--motivo", help="estado: motivo del relevo")
    p.add_argument(
        "--desfase",
        type=int,
        default=0,
        help="segundos a sumar a X-Alma-Ts (más de 300 o menos de -300: Odoo "
        "tiene que rechazarlo)",
    )
    p.add_argument(
        "--firma-mala",
        action="store_true",
        help="firma con otro secreto: Odoo tiene que rechazarlo",
    )
    p.add_argument(
        "--pausa", type=float, default=1.0, help="segundos entre eventos (1)"
    )
    p.add_argument("--timeout", type=float, default=15.0)
    p.add_argument(
        "--solo-imprimir",
        action="store_true",
        help="enseña las peticiones sin enviarlas",
    )
    return p.parse_args(argv)


def main(argv=None):
    args = argumentos(argv)
    if not args.numero:
        sys.exit("Falta el numero de la pasarela: --numero o ALMA_NUMERO.")
    args.numero = "".join(c for c in args.numero if c.isdigit())
    if not args.solo_imprimir and not (args.url and args.secreto):
        sys.exit(
            "Para enviar hacen falta la URL de entrada y el secreto "
            "(--url/ALMA_URL_ENTRADA y ALMA_SECRETO). Con --solo-imprimir no."
        )
    eventos = ESCENARIOS[args.escenario][0](args)
    for numero, datos in enumerate(eventos, 1):
        crudo = cuerpo_crudo(datos)
        cab = cabeceras(args.secreto, crudo, args.desfase, args.firma_mala)
        print(f"--- evento {numero}/{len(eventos)}: {datos['tipo']}")
        if args.solo_imprimir:
            imprimir(args.url, cab, crudo, args.secreto)
            continue
        try:
            codigo, texto = mandar(args.url, cab, crudo, args.timeout)
        except urllib.error.URLError as error:
            sys.exit(f"No se pudo conectar con {args.url}: {error.reason}")
        print(f"HTTP {codigo} {texto[:200]}")
        if numero < len(eventos):
            time.sleep(args.pausa)
    if not args.solo_imprimir:
        print(
            "\nRecuerde: el controlador contesta 200 {} aunque rechace la firma. "
            "Compruebe en Odoo que ha entrado."
        )


if __name__ == "__main__":
    main()
