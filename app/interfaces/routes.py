"""
Routers de FastAPI - Endpoints HTTP REST y WebSocket.
Define la API pública del microservicio de notificaciones.

Endpoints HTTP (requieren JWT en header Authorization):
  GET    /notificaciones/{usuario_id}              → Listar notificaciones (paginado)
  PATCH  /notificaciones/{notificacion_id}/leer     → Marcar notificación como leída
  GET    /notificaciones/{usuario_id}/no-leidas      → Conteo de notificaciones no leídas
  POST   /dispositivos/dispositivo                  → Registrar token FCM

Endpoints WebSocket (requieren JWT en query param ?token=):
  WS     /ws/{usuario_id}                           → Canal de notificaciones en tiempo real
"""
import logging
from urllib.parse import parse_qs

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Query,
    Request,
    WebSocket,
    WebSocketDisconnect,
    status,
)
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.application.services import NotificationService, DeviceService
from app.domain.models import (
    ConteoNoLeidas,
    DispositivoUsuario,
    MarcarLeidaRespuesta,
    NotificacionesPaginadas,
    RegistroDispositivoRequest,
)
from app.infrastructure.websocket_manager import WebSocketManager
from app.interfaces.auth import verificar_token, verificar_token_ws

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Routers
# ------------------------------------------------------------------

router_notificaciones = APIRouter(
    prefix="/notificaciones",
    tags=["Notificaciones"],
)

router_dispositivos = APIRouter(
    prefix="/dispositivos",
    tags=["Dispositivos"],
)

router_websocket = APIRouter(
    prefix="/ws",
    tags=["WebSocket"],
)

# Esquema de seguridad Bearer para Swagger UI
security_scheme = HTTPBearer(auto_error=True)


# ------------------------------------------------------------------
# Dependencias reutilizables
# ------------------------------------------------------------------

def _get_notification_service(request: Request) -> NotificationService:
    """Obtiene el NotificationService del estado de la aplicación."""
    return request.app.state.notification_service


def _get_device_service(request: Request) -> DeviceService:
    """Obtiene el DeviceService del estado de la aplicación."""
    return request.app.state.device_service


def _get_ws_manager(request: Request) -> WebSocketManager:
    """Obtiene el WebSocketManager del estado de la aplicación."""
    return request.app.state.ws_manager


def _verificar_propiedad_token(payload: dict, usuario_id: str) -> None:
    """
    Verifica que el usuario del token sea el mismo que el recurso solicitado.
    El claim 'sub' del JWT debe coincidir con el usuario_id de la ruta.

    Args:
        payload: Payload decodificado del JWT.
        usuario_id: ID de usuario de la ruta.

    Raises:
        HTTPException 403 si no hay coincidencia.
    """
    token_user_id = payload.get("sub")
    if not token_user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sin claim 'sub'")
    if token_user_id != usuario_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="No autorizado para acceder a recursos de otro usuario",
        )


# ==================================================================
# Endpoints de Notificaciones
# ==================================================================

@router_notificaciones.get(
    "/{usuario_id}",
    response_model=NotificacionesPaginadas,
    summary="Listar notificaciones del usuario",
    description="Obtiene las notificaciones de un usuario de forma paginada, ordenadas por fecha descendente.",
)
async def listar_notificaciones(
    usuario_id: str,
    pagina: int = Query(1, ge=1, description="Número de página (comienza en 1)"),
    tamano: int = Query(20, ge=1, le=100, description="Cantidad de notificaciones por página"),
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    notification_service: NotificationService = Depends(_get_notification_service),
) -> NotificacionesPaginadas:
    """GET /notificaciones/{usuario_id}?pagina=1&tamano=20"""
    payload = await verificar_token(credentials.credentials)
    _verificar_propiedad_token(payload, usuario_id)
    return await notification_service.obtener_notificaciones(usuario_id, pagina, tamano)


@router_notificaciones.patch(
    "/{notificacion_id}/leer",
    response_model=MarcarLeidaRespuesta,
    summary="Marcar notificación como leída",
    description="Cambia el estado de una notificación a 'leída'.",
)
async def marcar_notificacion_leida(
    notificacion_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    notification_service: NotificationService = Depends(_get_notification_service),
) -> MarcarLeidaRespuesta:
    """PATCH /notificaciones/{notificacion_id}/leer"""
    await verificar_token(credentials.credentials)

    actualizada = await notification_service.marcar_como_leida(notificacion_id)
    if not actualizada:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Notificación '{notificacion_id}' no encontrada",
        )

    return MarcarLeidaRespuesta(
        mensaje="Notificación marcada como leída",
        id=notificacion_id,
    )


@router_notificaciones.get(
    "/{usuario_id}/no-leidas",
    response_model=ConteoNoLeidas,
    summary="Conteo de notificaciones no leídas",
    description="Retorna el número de notificaciones sin leer del usuario.",
)
async def conteo_no_leidas(
    usuario_id: str,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    notification_service: NotificationService = Depends(_get_notification_service),
) -> ConteoNoLeidas:
    """GET /notificaciones/{usuario_id}/no-leidas"""
    payload = await verificar_token(credentials.credentials)
    _verificar_propiedad_token(payload, usuario_id)
    return await notification_service.conteo_no_leidas(usuario_id)


# ==================================================================
# Endpoint de Dispositivos
# ==================================================================

@router_dispositivos.post(
    "/dispositivo",
    response_model=DispositivoUsuario,
    status_code=status.HTTP_200_OK,
    summary="Registrar dispositivo para push notifications",
    description="Registra o actualiza el token FCM de un dispositivo móvil/web del usuario.",
)
async def registrar_dispositivo(
    body: RegistroDispositivoRequest,
    credentials: HTTPAuthorizationCredentials = Depends(security_scheme),
    device_service: DeviceService = Depends(_get_device_service),
) -> DispositivoUsuario:
    """POST /dispositivos/dispositivo"""
    payload = await verificar_token(credentials.credentials)
    _verificar_propiedad_token(payload, body.usuario_id)
    return await device_service.registrar_dispositivo(body)


# ==================================================================
# Endpoint WebSocket
# ==================================================================

@router_websocket.websocket("/{usuario_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    usuario_id: str,
    ws_manager: WebSocketManager = Depends(_get_ws_manager),
) -> None:
    """
    Canal WebSocket por usuario para notificaciones en tiempo real.

    Conexión: ws://host/ws/{usuario_id}?token=<JWT>

    Comportamiento:
    - El servidor envía notificaciones en tiempo real cuando se procesan eventos.
    - El cliente puede enviar "ping" para mantener viva la conexión.
    - El servidor responde "pong" a cada ping.
    - Si el token es inválido, la conexión se cierra inmediatamente.
    """
    # --- Autenticación vía query parameter ---
    query_string = websocket.scope.get("query_string", b"").decode()
    query_params = parse_qs(query_string)
    token = query_params.get("token", [None])[0]

    if not token:
        await websocket.close(code=4001, reason="Token JWT requerido en query param '?token='")
        return

    try:
        payload = await verificar_token_ws(token)
        token_user_id = payload.get("sub")
        if token_user_id != usuario_id:
            await websocket.close(
                code=4003,
                reason=f"El token no pertenece al usuario '{usuario_id}'",
            )
            return
    except ValueError as exc:
        await websocket.close(code=4001, reason=str(exc))
        return

    # --- Conexión aceptada ---
    await websocket.accept()
    await ws_manager.conectar(usuario_id, websocket)
    logger.info("WebSocket aceptado para usuario %s", usuario_id)

    try:
        # Mantener la conexión abierta; escuchar pings del cliente
        while True:
            data = await websocket.receive_text()
            if data == "ping":
                await websocket.send_text("pong")
            # Se pueden agregar más comandos aquí en el futuro
    except WebSocketDisconnect:
        logger.info("WebSocket desconectado por el cliente: usuario %s", usuario_id)
    except Exception as exc:
        logger.error("Error en WebSocket de usuario %s: %s", usuario_id, exc)
    finally:
        await ws_manager.desconectar(usuario_id, websocket)
