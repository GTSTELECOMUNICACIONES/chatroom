# ChatRoom de GTS

Módulo de Odoo para atender WhatsApp desde Odoo, contra el portal de Alma.

**Arquitectura decidida el 19-11-2026** (ver la memoria de Óscar,
`chatroom-19-arranque.md`, que tiene el traspaso completo):

- Se construye **sobre el `mail_gateway` de la OCA** (`github.com/OCA/mail`,
  rama 19.0), **por dependencia y no copiándolo**. Es un sistema de enchufes:
  `gateway_type` es una Selection vacía y el base resuelve
  `self.env[f"mail.gateway.{gateway_type}"]`. Añadimos el tipo `alma` sin
  tocar nada suyo, así que sus actualizaciones no pueden pisarnos.
- **Licencia AGPL-3**, heredada de ellos.
- **El repositorio es PÚBLICO.** La dirección del portal y las claves van en
  campos de configuración, **nunca en el código**: así un módulo que se lleven
  no sirve de nada sin saber contra qué conecta.

## Antes de subir

    ~/venv/bin/python tools/sin_secretos.py

Impide subir la dirección del portal, claves, secretos escritos a mano y
teléfonos españoles reales. En un repositorio público un descuido no se
arregla borrando el commit: para cuando se ve, ya está copiado e indexado.
