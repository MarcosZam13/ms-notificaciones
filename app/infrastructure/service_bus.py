"""
Listener de Azure Service Bus para procesar eventos de forma asíncrona.
Se ejecuta como tarea en background al iniciar FastAPI.

Flujo:
1. Recibe mensajes de la cola configurada.
2. Decodifica el JSON del cuerpo del mensaje.
3. Procesa el evento a través del NotificationService.
4. Confirma (completa) el mensaje si se procesó correctamente.
5. Envía a dead-letter si el mensaje es inválido o excede reintentos.
"""
import asyncio
import json
import logging
from typing import Optional

from azure.servicebus.aio import ServiceBusClient, ServiceBusReceiver
from azure.servicebus import ServiceBusMessage
from azure.servicebus.exceptions import ServiceBusError

from app.domain.models import EventoServiceBus

logger = logging.getLogger(__name__)

# Número máximo de reintentos antes de enviar a dead-letter
MAX_DELIVERY_COUNT = 3
# Tiempo de espera entre reintentos en caso de error de conexión
RECONNECT_DELAY_SECONDS = 5


class ServiceBusListener:
    """
    Escucha y procesa mensajes de Azure Service Bus en una tarea asíncrona de background.
    Se conecta a una cola específica y procesa cada mensaje secuencialmente.
    """

    def __init__(self, connection_string: str, topic_name: str, subscription_name: str) -> None:
        """
        Args:
            connection_string: Cadena de conexión de Azure Service Bus.
            topic_name: Nombre del topic a escuchar (ej. "mensajes-eventos").
            subscription_name: Nombre de la suscripción en ese topic (ej. "notificaciones-sub").
        """
        self._connection_string = connection_string
        self._topic_name = topic_name
        self._subscription_name = subscription_name
        self._notification_service = None  # Se inyecta después con setter
        self._client: Optional[ServiceBusClient] = None
        self._running = False
        self._task: Optional[asyncio.Task] = None

    # ------------------------------------------------------------------
    # Inyección de dependencias
    # ------------------------------------------------------------------

    def set_notification_service(self, service) -> None:
        """
        Establece el servicio de notificaciones después de la inicialización.
        Necesario porque el servicio se crea después del listener.

        Args:
            service: Instancia de NotificationService.
        """
        self._notification_service = service

    # ------------------------------------------------------------------
    # Control del ciclo de vida
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """
        Inicia el listener en una tarea asíncrona de background.
        Es idempotente: si ya está corriendo, no hace nada.
        """
        if self._running:
            logger.warning("El listener de Service Bus ya está en ejecución")
            return

        self._running = True
        self._client = ServiceBusClient.from_connection_string(self._connection_string)
        self._task = asyncio.create_task(self._loop_escucha())
        logger.info(
            "Listener de Service Bus iniciado. Topic: %s / Suscripción: %s",
            self._topic_name, self._subscription_name,
        )

    async def stop(self) -> None:
        """
        Detiene el listener de forma ordenada.
        Cancela la tarea y cierra el cliente.
        """
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._client is not None:
            await self._client.close()
            self._client = None

        logger.info("Listener de Service Bus detenido")

    # ------------------------------------------------------------------
    # Bucle principal de escucha
    # ------------------------------------------------------------------

    async def _loop_escucha(self) -> None:
        """
        Bucle principal que mantiene la conexión y recibe mensajes continuamente.
        En caso de error de conexión, espera RECONNECT_DELAY_SECONDS y reintenta.
        """
        while self._running:
            try:
                async with self._client:
                    receiver: ServiceBusReceiver = self._client.get_subscription_receiver(
                        topic_name=self._topic_name,
                        subscription_name=self._subscription_name,
                        max_wait_time=5,  # segundos de espera máxima por lote
                    )
                    async with receiver:
                        await self._recibir_mensajes(receiver)
            except asyncio.CancelledError:
                break
            except (ServiceBusError, OSError) as exc:
                logger.error("Error de conexión con Service Bus: %s. Reintentando en %ds...",
                             exc, RECONNECT_DELAY_SECONDS)
                if self._running:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)
            except Exception as exc:
                logger.exception("Error inesperado en el listener de Service Bus: %s", exc)
                if self._running:
                    await asyncio.sleep(RECONNECT_DELAY_SECONDS)

    async def _recibir_mensajes(self, receiver: ServiceBusReceiver) -> None:
        """
        Recibe mensajes en lotes y los procesa uno por uno.

        Args:
            receiver: Receptor de la cola de Service Bus.
        """
        while self._running:
            # Recibir lote de mensajes (máximo 10 por lote, espera máxima 5 segundos)
            messages = await receiver.receive_messages(
                max_message_count=10,
                max_wait_time=5,
            )

            for message in messages:
                await self._procesar_mensaje(message, receiver)

            # Pequeña pausa para no saturar la CPU cuando no hay mensajes
            await asyncio.sleep(0.1)

    # ------------------------------------------------------------------
    # Procesamiento de mensajes individuales
    # ------------------------------------------------------------------

    async def _procesar_mensaje(self, message: ServiceBusMessage, receiver: ServiceBusReceiver) -> None:
        """
        Procesa un mensaje individual: decodifica JSON, valida, procesa y confirma.

        Estrategia de error:
        - JSON inválido → dead-letter inmediato
        - Error de procesamiento → abandonar (reintentar) hasta MAX_DELIVERY_COUNT, luego dead-letter

        Args:
            message: Mensaje recibido de Service Bus.
            receiver: Receptor para confirmar/abandonar mensajes.
        """
        try:
            # Decodificar el cuerpo del mensaje.
            # El SDK de Azure Service Bus puede devolver:
            #   - bytes / bytearray  → decodificar directamente
            #   - str                → usar tal cual
            #   - generator[bytes]   → unir chunks y decodificar (caso más común en aio)
            cuerpo = message.body
            if isinstance(cuerpo, (bytes, bytearray)):
                cuerpo = cuerpo.decode("utf-8")
            elif not isinstance(cuerpo, str):
                # Generator u otro iterable de bytes chunks
                cuerpo = b"".join(cuerpo).decode("utf-8")

            data = json.loads(cuerpo)

            # Validar con el modelo de dominio
            evento = EventoServiceBus(**data)

            logger.info(
                "Mensaje recibido: tipo=%s, usuario=%s, id_mensaje=%s",
                evento.tipo, evento.destinatario_id, message.message_id,
            )

            # Procesar el evento a través del servicio de notificaciones
            if self._notification_service is not None:
                await self._notification_service.procesar_evento(evento)
            else:
                logger.error("NotificationService no configurado en el listener")

            # Confirmar (completar) el mensaje - se elimina de la cola
            await receiver.complete_message(message)
            logger.debug("Mensaje %s completado exitosamente", message.message_id)

        except json.JSONDecodeError as exc:
            logger.error("JSON inválido en mensaje %s: %s", message.message_id, exc)
            await receiver.dead_letter_message(
                message,
                reason="JSON inválido",
                error_description=str(exc),
            )

        except Exception as exc:
            logger.error("Error al procesar mensaje %s: %s", message.message_id, exc)
            delivery_count = getattr(message, "delivery_count", 0) or 0

            if delivery_count >= MAX_DELIVERY_COUNT:
                # Excedió reintentos → dead-letter
                await receiver.dead_letter_message(
                    message,
                    reason=f"Excedidos {MAX_DELIVERY_COUNT} reintentos",
                    error_description=str(exc),
                )
            else:
                # Reintentar (devolver a la cola)
                await receiver.abandon_message(message)
