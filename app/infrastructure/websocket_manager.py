"""
Gestor de conexiones WebSocket para notificaciones en tiempo real.
Administra conexiones activas por usuario y permite enviar mensajes
a usuarios específicos o hacer broadcast a todos los conectados.
"""
import asyncio
import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Administra las conexiones WebSocket activas agrupadas por usuario.
    Soporta múltiples conexiones por usuario (ej. varias pestañas del navegador).

    Thread-safe: usa asyncio.Lock para operaciones concurrentes.
    """

    def __init__(self) -> None:
        # usuario_id → conjunto de conexiones WebSocket activas
        self._conexiones: dict[str, set[WebSocket]] = {}
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # Gestión de conexiones
    # ------------------------------------------------------------------

    async def conectar(self, usuario_id: str, websocket: WebSocket) -> None:
        """
        Registra una nueva conexión WebSocket para un usuario.

        Args:
            usuario_id: ID del usuario que se conecta.
            websocket: Instancia de WebSocket aceptada.
        """
        async with self._lock:
            if usuario_id not in self._conexiones:
                self._conexiones[usuario_id] = set()
            self._conexiones[usuario_id].add(websocket)
            logger.info(
                "WebSocket conectado: usuario=%s (total=%d conexiones, %d usuarios)",
                usuario_id,
                len(self._conexiones[usuario_id]),
                len(self._conexiones),
            )

    async def desconectar(self, usuario_id: str, websocket: WebSocket) -> None:
        """
        Elimina una conexión WebSocket de un usuario.
        Si era la última conexión, limpia la entrada del usuario.

        Args:
            usuario_id: ID del usuario.
            websocket: Conexión a eliminar.
        """
        async with self._lock:
            if usuario_id in self._conexiones:
                self._conexiones[usuario_id].discard(websocket)
                if not self._conexiones[usuario_id]:
                    del self._conexiones[usuario_id]
                logger.debug("WebSocket desconectado: usuario=%s", usuario_id)

    # ------------------------------------------------------------------
    # Envío de mensajes
    # ------------------------------------------------------------------

    async def enviar_a_usuario(self, usuario_id: str, mensaje: dict[str, Any]) -> None:
        """
        Envía un mensaje JSON a todas las conexiones activas de un usuario.
        Si una conexión falla, se elimina automáticamente.

        Args:
            usuario_id: ID del usuario destinatario.
            mensaje: Diccionario a enviar como JSON.
        """
        # Obtener copia de las conexiones para no mantener el lock durante el envío
        async with self._lock:
            conexiones = self._conexiones.get(usuario_id, set()).copy()

        if not conexiones:
            logger.debug("Sin conexiones WebSocket activas para usuario %s", usuario_id)
            return

        # Enviar a cada conexión; si falla, marcarla para eliminar
        desconectadas: list[WebSocket] = []
        for ws in conexiones:
            try:
                await ws.send_json(mensaje)
            except Exception:
                logger.debug("Error al enviar mensaje WS a usuario %s, marcando para eliminar", usuario_id)
                desconectadas.append(ws)

        # Limpiar conexiones fallidas
        if desconectadas:
            async with self._lock:
                if usuario_id in self._conexiones:
                    for ws in desconectadas:
                        self._conexiones[usuario_id].discard(ws)
                    if not self._conexiones[usuario_id]:
                        del self._conexiones[usuario_id]

    async def broadcast(self, mensaje: dict[str, Any]) -> None:
        """
        Envía un mensaje a todos los usuarios conectados.

        Args:
            mensaje: Diccionario a enviar como JSON.
        """
        async with self._lock:
            usuarios = list(self._conexiones.keys())

        for usuario_id in usuarios:
            await self.enviar_a_usuario(usuario_id, mensaje)

    # ------------------------------------------------------------------
    # Métricas y estado
    # ------------------------------------------------------------------

    @property
    def conexiones_activas(self) -> int:
        """Número total de conexiones WebSocket activas."""
        return sum(len(conns) for conns in self._conexiones.values())

    @property
    def usuarios_conectados(self) -> int:
        """Número de usuarios con al menos una conexión WebSocket activa."""
        return len(self._conexiones)

    async def esta_conectado(self, usuario_id: str) -> bool:
        """
        Verifica si un usuario tiene al menos una conexión WebSocket activa.

        Args:
            usuario_id: ID del usuario.

        Returns:
            True si el usuario está conectado.
        """
        async with self._lock:
            return usuario_id in self._conexiones and len(self._conexiones[usuario_id]) > 0
