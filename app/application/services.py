"""
Servicios de aplicación - Casos de uso del negocio.
Orquesta la lógica entre dominio e infraestructura siguiendo el patrón
de arquitectura hexagonal (puertos y adaptadores).
"""
import logging
from datetime import datetime, timezone

from app.domain.models import (
    EventoServiceBus,
    Notificacion,
    NotificacionRespuesta,
    NotificacionesPaginadas,
    DispositivoUsuario,
    ConteoNoLeidas,
    RegistroDispositivoRequest,
)
from app.infrastructure.database import CosmosDBClient
from app.infrastructure.fcm_client import FCMClient
from app.infrastructure.websocket_manager import WebSocketManager

logger = logging.getLogger(__name__)


class NotificationService:
    """
    Servicio de notificaciones: orquesta la lógica de negocio.
    Coordina persistencia, emisión en tiempo real y push notifications.
    """

    def __init__(
        self,
        db: CosmosDBClient,
        fcm: FCMClient,
        ws_manager: WebSocketManager,
    ) -> None:
        self._db = db
        self._fcm = fcm
        self._ws_manager = ws_manager

    # ------------------------------------------------------------------
    # Procesamiento de eventos entrantes (origen: Service Bus)
    # ------------------------------------------------------------------

    async def procesar_evento(self, evento: EventoServiceBus) -> NotificacionRespuesta:
        """
        Procesa un evento entrante realizando tres acciones:
        1. Persiste la notificación en Cosmos DB con estado 'no_leida'.
        2. Emite en tiempo real vía WebSocket al cliente web conectado.
        3. Envía push notification vía FCM al dispositivo móvil del usuario.

        Args:
            evento: Mensaje recibido desde Azure Service Bus.

        Returns:
            NotificacionRespuesta con los datos de la notificación creada.
        """
        # 1. Construir el modelo de persistencia
        notificacion = Notificacion(
            usuario_id=evento.destinatario_id,
            tipo=evento.tipo,
            titulo=evento.titulo,
            cuerpo=evento.cuerpo,
            metadata=evento.metadata,
            leida=False,
            creada_en=datetime.now(timezone.utc),
        )

        # 2. Persistir en Cosmos DB
        notificacion_id = await self._db.insertar_notificacion(notificacion)
        logger.info(
            "Notificación persistida: id=%s, usuario=%s, tipo=%s",
            str(notificacion_id), evento.destinatario_id, evento.tipo,
        )

        # 3. Preparar payload común para WebSocket y push
        payload_notificacion = {
            "id": str(notificacion_id),
            "usuario_id": evento.destinatario_id,
            "tipo": evento.tipo,
            "titulo": evento.titulo,
            "cuerpo": evento.cuerpo,
            "metadata": evento.metadata,
            "leida": False,
            "creada_en": notificacion.creada_en.isoformat(),
        }

        # 4. Emitir en tiempo real vía WebSocket (fire-and-forget con manejo de error)
        try:
            await self._ws_manager.enviar_a_usuario(evento.destinatario_id, payload_notificacion)
            logger.debug("Notificación WS emitida a usuario %s", evento.destinatario_id)
        except Exception as exc:
            logger.error("Error al emitir vía WebSocket a usuario %s: %s", evento.destinatario_id, exc)

        # 5. Enviar push notification vía FCM (fire-and-forget con manejo de error)
        try:
            await self._fcm.enviar_notificacion(
                usuario_id=evento.destinatario_id,
                titulo=evento.titulo,
                cuerpo=evento.cuerpo,
                data=evento.metadata,
            )
        except Exception as exc:
            logger.error("Error al enviar push a usuario %s: %s", evento.destinatario_id, exc)

        return NotificacionRespuesta(**payload_notificacion)

    # ------------------------------------------------------------------
    # Consultas
    # ------------------------------------------------------------------

    async def obtener_notificaciones(
        self,
        usuario_id: str,
        pagina: int = 1,
        tamano_pagina: int = 20,
    ) -> NotificacionesPaginadas:
        """
        Obtiene las notificaciones de un usuario de forma paginada,
        ordenadas por fecha de creación descendente (más recientes primero).

        Args:
            usuario_id: ID del usuario.
            pagina: Número de página (1-indexado).
            tamano_pagina: Cantidad de notificaciones por página (1-100).

        Returns:
            NotificacionesPaginadas con la lista de items y metadatos de paginación.
        """
        skip = (pagina - 1) * tamano_pagina
        docs, total = await self._db.obtener_notificaciones(usuario_id, skip, tamano_pagina)

        items = [NotificacionRespuesta(**doc) for doc in docs]
        total_paginas = max((total + tamano_pagina - 1) // tamano_pagina, 1)

        return NotificacionesPaginadas(
            items=items,
            total=total,
            pagina=pagina,
            tamano_pagina=tamano_pagina,
            total_paginas=total_paginas,
        )

    async def marcar_como_leida(self, notificacion_id: str) -> bool:
        """
        Marca una notificación como leída.

        Args:
            notificacion_id: ID de la notificación a marcar.

        Returns:
            True si se actualizó correctamente, False si no se encontró.
        """
        return await self._db.marcar_leida(notificacion_id)

    async def conteo_no_leidas(self, usuario_id: str) -> ConteoNoLeidas:
        """
        Cuenta el número de notificaciones no leídas de un usuario.

        Args:
            usuario_id: ID del usuario.

        Returns:
            ConteoNoLeidas con el total de notificaciones pendientes.
        """
        count = await self._db.contar_no_leidas(usuario_id)
        return ConteoNoLeidas(usuario_id=usuario_id, no_leidas=count)


# ============================================================
# Servicio de Dispositivos
# ============================================================

class DeviceService:
    """
    Servicio de gestión de dispositivos para notificaciones push.
    Maneja el registro y actualización de tokens FCM.
    """

    def __init__(self, db: CosmosDBClient) -> None:
        self._db = db

    async def registrar_dispositivo(self, request: RegistroDispositivoRequest) -> DispositivoUsuario:
        """
        Registra o actualiza el token FCM de un dispositivo.
        Si ya existe un registro para el usuario, se sobrescribe con el nuevo token.

        Args:
            request: Datos del dispositivo a registrar.

        Returns:
            DispositivoUsuario con los datos registrados.
        """
        dispositivo = DispositivoUsuario(
            usuario_id=request.usuario_id,
            fcm_token=request.fcm_token,
            plataforma=request.plataforma,
            actualizado_en=datetime.now(timezone.utc),
        )
        await self._db.upsert_dispositivo(dispositivo)
        logger.info("Dispositivo registrado para usuario %s en plataforma %s", request.usuario_id, request.plataforma)
        return dispositivo
