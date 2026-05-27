"""
Cliente de Firebase Cloud Messaging (FCM) para notificaciones push.
Envía notificaciones push a dispositivos Android/iOS registrados.

Requisitos:
- Archivo JSON de credenciales de cuenta de servicio de Firebase.
- firebase-admin SDK instalado.
"""
import logging
import os
from typing import Any, Optional

import firebase_admin
from firebase_admin import credentials, messaging
from firebase_admin.exceptions import FirebaseError

from app.infrastructure.database import CosmosDBClient

logger = logging.getLogger(__name__)

# Nombre del canal de notificación en Android
ANDROID_CHANNEL_ID = "notificaciones_arrendamientos"


class FCMClient:
    """
    Cliente para enviar notificaciones push vía Firebase Cloud Messaging.
    Soporta inicialización desde archivo JSON de credenciales.
    Si no hay credenciales disponibles, opera en modo degradado (solo logging).
    """

    def __init__(self, db: CosmosDBClient, credentials_path: Optional[str] = None) -> None:
        """
        Args:
            db: Cliente de base de datos para consultar tokens FCM.
            credentials_path: Ruta al archivo JSON de credenciales de Firebase.
                              Si es None o no existe, se opera en modo degradado.
        """
        self._db = db
        self._inicializado = False

        # Intentar inicializar Firebase Admin SDK
        if credentials_path and os.path.isfile(credentials_path):
            try:
                cred = credentials.Certificate(credentials_path)
                firebase_admin.initialize_app(cred)
                self._inicializado = True
                logger.info("FCM inicializado correctamente desde: %s", credentials_path)
            except ValueError as exc:
                # Ya estaba inicializado (posible en tests o recarga)
                if "The default Firebase app already exists" in str(exc):
                    self._inicializado = True
                    logger.info("FCM: usando app de Firebase ya existente")
                else:
                    logger.warning("Error al inicializar Firebase: %s", exc)
            except Exception as exc:
                logger.warning("No se pudo inicializar FCM: %s. Push notifications deshabilitadas.", exc)
        else:
            logger.warning(
                "Credenciales FCM no encontradas en '%s'. Las notificaciones push estarán deshabilitadas.",
                credentials_path or "N/A",
            )

    # ------------------------------------------------------------------
    # Envío de notificaciones push
    # ------------------------------------------------------------------

    async def enviar_notificacion(
        self,
        usuario_id: str,
        titulo: str,
        cuerpo: str,
        data: Optional[dict[str, Any]] = None,
    ) -> bool:
        """
        Envía una notificación push al dispositivo registrado del usuario.

        Args:
            usuario_id: ID del usuario destinatario.
            titulo: Título de la notificación.
            cuerpo: Texto del cuerpo de la notificación.
            data: Datos adicionales a enviar como payload (opcional).

        Returns:
            True si se envió correctamente, False en caso contrario.
        """
        if not self._inicializado:
            logger.debug("FCM no inicializado, omitiendo push para usuario %s", usuario_id)
            return False

        # Buscar el dispositivo registrado del usuario
        dispositivo = await self._db.obtener_dispositivo(usuario_id)
        if not dispositivo:
            logger.debug("Sin dispositivo registrado para usuario %s", usuario_id)
            return False

        fcm_token = dispositivo.get("fcm_token", "")
        if not fcm_token:
            logger.debug("Token FCM vacío para usuario %s", usuario_id)
            return False

        # Construir el mensaje FCM
        try:
            message = messaging.Message(
                notification=messaging.Notification(
                    title=titulo,
                    body=cuerpo,
                ),
                data=self._formatear_data(data),
                token=fcm_token,
                android=messaging.AndroidConfig(
                    priority="high",
                    notification=messaging.AndroidNotification(
                        channel_id=ANDROID_CHANNEL_ID,
                        click_action="FLUTTER_NOTIFICATION_CLICK",
                        priority="high",
                        visibility="public",
                    ),
                ),
                # Configuración para APNs (iOS)
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            alert=messaging.ApsAlert(
                                title=titulo,
                                body=cuerpo,
                            ),
                            sound="default",
                            badge=1,
                            content_available=True,
                        ),
                    ),
                ),
            )

            response = messaging.send(message)
            logger.info("Push enviada a usuario %s: %s", usuario_id, response)
            return True

        except messaging.UnregisteredError:
            logger.warning(
                "Token FCM no registrado/inválido para usuario %s. Se debería eliminar de la BD.",
                usuario_id,
            )
            return False
        except FirebaseError as exc:
            logger.error("Error de Firebase al enviar push a usuario %s: %s", usuario_id, exc)
            return False
        except Exception as exc:
            logger.exception("Error inesperado al enviar push a usuario %s: %s", usuario_id, exc)
            return False

    # ------------------------------------------------------------------
    # Utilidades
    # ------------------------------------------------------------------

    @staticmethod
    def _formatear_data(data: Optional[dict[str, Any]]) -> dict[str, str]:
        """
        Convierte el diccionario de metadatos a un formato compatible con FCM.
        FCM solo acepta valores string en el campo 'data'.

        Args:
            data: Diccionario de metadatos original.

        Returns:
            Diccionario con todos los valores convertidos a string.
        """
        if not data:
            return {}

        result: dict[str, str] = {}
        for key, value in data.items():
            if isinstance(value, (dict, list)):
                import json
                result[key] = json.dumps(value, ensure_ascii=False)
            else:
                result[key] = str(value)
        return result

    @property
    def inicializado(self) -> bool:
        """Indica si el cliente FCM está correctamente inicializado."""
        return self._inicializado
