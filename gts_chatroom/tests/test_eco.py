"""El bucle de eco del 21-11-2026: lo que entra del portal no vuelve a salir.

Sin red: `requests.request` se sustituye por un doble que cuenta las llamadas.
En las pruebas no hay commit, asi que `_send` envia en el acto en vez de en
`postcommit` (igual que `mail.mail`).
"""

from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged

from odoo.addons.gts_chatroom.models.mail_gateway_alma import _fecha

REQUEST = "odoo.addons.gts_chatroom.models.mail_gateway_alma.requests.request"
NUMERO = "15555550100"
TELEFONO = "15555550123"


def _respuesta(codigo, datos):
    respuesta = MagicMock()
    respuesta.status_code = codigo
    respuesta.json.return_value = datos
    respuesta.headers = {}
    respuesta.text = ""
    return respuesta


@tagged("post_install", "-at_install")
class TestEco(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gateway = cls.env["mail.gateway"].create(
            {
                "name": "Alma de prueba",
                "token": "token-de-prueba-eco",
                "gateway_type": "alma",
                "alma_url": "https://portal.invalid",
                "alma_numero": NUMERO,
            }
        )
        cls.gateway.integrated_webhook_state = "integrated"
        cls.Alma = cls.env["mail.gateway.alma"]
        cls.canal = cls.Alma._get_channel(
            cls.gateway,
            TELEFONO,
            {"telefono": TELEFONO, "nombre": "Cliente de prueba"},
            force_create=True,
        )

    def _evento_mensaje(self, mensaje_id, de="operador", texto="Hola"):
        return {
            "tipo": "mensaje",
            "emitido_en": "2026-11-21T21:43:29Z",
            "numero": NUMERO,
            "conversacion": {
                "id": "conv-prueba",
                "contacto": {"telefono": TELEFONO, "nombre": "Cliente de prueba"},
            },
            "mensaje": {"id": mensaje_id, "de": de, "texto": texto, "adjuntos": []},
        }

    def _mensajes(self):
        return self.env["mail.message"].search(
            [
                ("model", "=", "discuss.channel"),
                ("res_id", "=", self.canal.id),
                ("message_type", "=", "comment"),
            ]
        )

    def test_operador_del_portal_se_publica_y_no_sale(self):
        """El caso del bucle: un `mensaje` con de=operador entra y NO llama a
        /enviar, aunque se procese sin el contexto del controlador."""
        with patch(REQUEST) as peticion:
            self.Alma._alma_procesar(self.gateway, self._evento_mensaje("p-1"))
        peticion.assert_not_called()
        nuevo = self._mensajes().filtered(lambda m: m.alma_mensaje_id == "p-1")
        self.assertEqual(len(nuevo), 1)
        self.assertEqual(nuevo.alma_origen, "operador")
        self.assertFalse(nuevo.gateway_notification_ids)

    def test_red_de_seguridad_en_send(self):
        """Si un mensaje del portal llega a `_send` por otro camino, no sale."""
        with patch(REQUEST) as peticion:
            self.Alma._alma_procesar(self.gateway, self._evento_mensaje("p-2"))
            entrado = self._mensajes().filtered(lambda m: m.alma_mensaje_id == "p-2")
            notificacion = self.env["mail.notification"].create(
                {
                    "mail_message_id": entrado.id,
                    "gateway_channel_id": self.canal.id,
                    "notification_type": "gateway",
                    "gateway_type": "alma",
                }
            )
            notificacion.send_gateway()
        peticion.assert_not_called()
        self.assertEqual(notificacion.notification_status, "canceled")

    def test_eco_de_lo_que_mando_odoo_no_se_duplica(self):
        """Odoo envia, el portal devuelve el eco con el mismo id: no se publica
        otra vez. El ACUSE con ese id si llega a su notificacion."""
        with patch(
            REQUEST,
            return_value=_respuesta(202, {"mensaje_id": "o-1", "estado": "en_cola"}),
        ) as peticion:
            self.canal.message_post(
                body="Hola en que podemos ayudarte", message_type="comment"
            )
            self.assertEqual(peticion.call_count, 1)
            antes = len(self._mensajes())
            self.Alma._alma_procesar(self.gateway, self._evento_mensaje("o-1"))
            self.assertEqual(len(self._mensajes()), antes)
            self.Alma._alma_procesar(
                self.gateway,
                {
                    "tipo": "acuse",
                    "emitido_en": "2026-11-21T21:43:40Z",
                    "numero": NUMERO,
                    "acuse": {"mensaje_id": "o-1", "estado": "entregado"},
                },
            )
            # Ni el eco ni el acuse vuelven a llamar al portal.
            self.assertEqual(peticion.call_count, 1)
        notificacion = self.env["mail.notification"].search(
            [("gateway_message_id", "=", "o-1")]
        )
        self.assertEqual(notificacion.alma_estado, "entregado")

    def test_desintegrada_no_envia(self):
        self.gateway.integrated_webhook_state = False
        with patch(REQUEST) as peticion:
            mensaje = self.canal.message_post(
                body="No debe salir", message_type="comment"
            )
        peticion.assert_not_called()
        self.assertEqual(
            mensaje.gateway_notification_ids.notification_status, "exception"
        )

    def test_posible_bucle_tiene_texto(self):
        with patch(
            REQUEST,
            return_value=_respuesta(
                429, {"alma_motivo": "posible_bucle", "error": "Frase del portal"}
            ),
        ):
            mensaje = self.canal.message_post(body="Otra vez", message_type="comment")
        notificacion = mensaje.gateway_notification_ids
        self.assertEqual(notificacion.alma_motivo, "posible_bucle")
        self.assertIn("parece un bucle", notificacion.failure_reason)

    def test_estado_de_desde_desatasca_un_envio(self):
        """/desde devuelve nuestro mensaje con su ultimo `mensaje.estado`: no
        se publica otra vez, pero su notificacion avanza."""
        with patch(
            REQUEST,
            return_value=_respuesta(202, {"mensaje_id": "o-2", "estado": "en_cola"}),
        ):
            self.canal.message_post(body="Hola", message_type="comment")
        antes = len(self._mensajes())
        evento = self._evento_mensaje("o-2")
        evento["mensaje"]["estado"] = "leido"
        with patch(REQUEST) as peticion:
            self.Alma._alma_procesar(self.gateway, evento)
        peticion.assert_not_called()
        self.assertEqual(len(self._mensajes()), antes)
        notificacion = self.env["mail.notification"].search(
            [("gateway_message_id", "=", "o-2")]
        )
        self.assertEqual(notificacion.alma_estado, "leido")


@tagged("post_install", "-at_install")
class TestAvisoEspera(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.gateway = cls.env["mail.gateway"].create(
            {
                "name": "Alma de prueba aviso",
                "token": "token-de-prueba-aviso",
                "gateway_type": "alma",
                "alma_url": "https://portal.invalid",
                "alma_numero": NUMERO,
                "member_ids": [(6, 0, [cls.env.ref("base.user_admin").id])],
            }
        )
        cls.canal = cls.env["mail.gateway.alma"]._get_channel(
            cls.gateway, TELEFONO, {"telefono": TELEFONO}, force_create=True
        )

    def test_avisa_solo_al_entrar_en_espera(self):
        Usuarios = type(self.env["res.users"])
        espera = {"relevo": "humano", "asignado_a": None, "espera": True}
        with patch.object(Usuarios, "_bus_send") as aviso:
            self.canal._alma_aplicar_estado(espera, _fecha("2026-11-21T22:00:00Z"))
            self.assertEqual(aviso.call_count, 1)
            self.assertEqual(aviso.call_args.args[0], "simple_notification")
            # Otro evento que sigue en espera: no vuelve a avisar.
            self.canal._alma_aplicar_estado(espera, _fecha("2026-11-21T22:01:00Z"))
            self.assertEqual(aviso.call_count, 1)
