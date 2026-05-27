"""
Microservicio de Notificaciones - Punto de entrada principal.

Aplicación FastAPI que expone endpoints REST y WebSocket para el sistema
de notificaciones asíncronas de la plataforma de arrendamientos.

Arquitectura:
- Escucha eventos de Azure Service Bus en background (asyncio task).
- Persiste notificaciones en Azure Cosmos DB (API MongoDB).
- Emite notificaciones en tiempo real vía WebSockets nativos de FastAPI.
- Envía push notifications vía Firebase Cloud Messaging (FCM).
- Expone API REST autenticada con JWT a través de Azure API Management (APIM).

Uso:
    uvicorn app.main:app --host 0.0.0.0 --port 8000
"""
import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.application.services import DeviceService, NotificationService
from app.domain.models import HealthResponse
from app.infrastructure.database import CosmosDBClient
from app.infrastructure.fcm_client import FCMClient
from app.infrastructure.service_bus import ServiceBusListener
from app.infrastructure.websocket_manager import WebSocketManager
from app.interfaces.routes import (
    router_dispositivos,
    router_notificaciones,
    router_websocket,
)

# ------------------------------------------------------------------
# Configuración inicial
# ------------------------------------------------------------------

load_dotenv()  # Carga variables desde .env (solo desarrollo local)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

# Suprimir logs muy verbosos de dependencias
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(logging.WARNING)
logging.getLogger("azure.servicebus").setLevel(logging.WARNING)
logging.getLogger("firebase_admin").setLevel(logging.WARNING)


# ------------------------------------------------------------------
# Ciclo de vida de la aplicación
# ------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Maneja el ciclo de vida de la aplicación FastAPI.
    Inicializa conexiones, servicios y tareas background al iniciar,
    y las cierra ordenadamente al detener.
    """
    # ================================================================
    # STARTUP
    # ================================================================
    logger.info("=" * 60)
    logger.info("Iniciando Microservicio de Notificaciones v1.0.0")
    logger.info("=" * 60)

    # --- 1. Conexión a Cosmos DB ---
    cosmos_connection_string = os.getenv("COSMOS_CONNECTION_STRING")
    cosmos_database = os.getenv("COSMOS_DATABASE", "notificaciones_db")

    if not cosmos_connection_string:
        raise RuntimeError(
            "Variable de entorno COSMOS_CONNECTION_STRING no configurada. "
            "Revise el archivo .env o la configuración del App Service."
        )

    cosmos_client = CosmosDBClient(cosmos_connection_string, cosmos_database)
    await cosmos_client.crear_indices()

    if await cosmos_client.verificar_conexion():
        logger.info("✓ Cosmos DB conectado: base de datos '%s'", cosmos_database)
    else:
        raise RuntimeError("No se pudo establecer conexión con Cosmos DB")

    # --- 2. Inicializar WebSocket Manager ---
    ws_manager = WebSocketManager()
    logger.info("✓ WebSocket Manager inicializado")

    # --- 3. Inicializar FCM Client ---
    fcm_credentials_path = os.getenv("FCM_CREDENTIALS_PATH")
    fcm_client = FCMClient(cosmos_client, fcm_credentials_path)
    if fcm_client.inicializado:
        logger.info("✓ FCM Client inicializado")
    else:
        logger.warning("⚠ FCM Client no inicializado - push notifications deshabilitadas")

    # --- 4. Inicializar Servicios de Aplicación ---
    notification_service = NotificationService(cosmos_client, fcm_client, ws_manager)
    device_service = DeviceService(cosmos_client)
    logger.info("✓ Servicios de aplicación inicializados")

    # --- 5. Iniciar Listener de Azure Service Bus ---
    sb_connection_string = os.getenv("SERVICE_BUS_CONNECTION_STRING")
    sb_topic_name = os.getenv("SERVICE_BUS_TOPIC_NAME")
    sb_subscription_name = os.getenv("SERVICE_BUS_SUBSCRIPTION_NAME")
    service_bus_listener = None

    if sb_connection_string and sb_topic_name and sb_subscription_name:
        service_bus_listener = ServiceBusListener(
            sb_connection_string, sb_topic_name, sb_subscription_name
        )
        service_bus_listener.set_notification_service(notification_service)
        await service_bus_listener.start()
        logger.info(
            "✓ Listener de Service Bus iniciado: topic '%s' / suscripción '%s'",
            sb_topic_name, sb_subscription_name,
        )
    else:
        logger.warning(
            "⚠ Service Bus no configurado. "
            "Variables requeridas: SERVICE_BUS_CONNECTION_STRING, "
            "SERVICE_BUS_TOPIC_NAME, SERVICE_BUS_SUBSCRIPTION_NAME"
        )

    # --- 6. Guardar en estado de la aplicación ---
    app.state.notification_service = notification_service
    app.state.device_service = device_service
    app.state.ws_manager = ws_manager
    app.state.cosmos_client = cosmos_client
    app.state.service_bus_listener = service_bus_listener

    logger.info("=" * 60)
    logger.info("Microservicio de Notificaciones iniciado exitosamente")
    logger.info("=" * 60)

    yield  # ← La aplicación se ejecuta aquí

    # ================================================================
    # SHUTDOWN
    # ================================================================
    logger.info("=" * 60)
    logger.info("Deteniendo Microservicio de Notificaciones...")

    # Detener listener de Service Bus
    if hasattr(app.state, 'service_bus_listener') and app.state.service_bus_listener:
        await app.state.service_bus_listener.stop()

    # Cerrar conexión a Cosmos DB
    if hasattr(app.state, 'cosmos_client') and app.state.cosmos_client:
        await app.state.cosmos_client.cerrar()

    logger.info("Microservicio de Notificaciones detenido correctamente")
    logger.info("=" * 60)


# ------------------------------------------------------------------
# Crear aplicación FastAPI
# ------------------------------------------------------------------

app = FastAPI(
    title="Microservicio de Notificaciones",
    description=(
        "Servicio de notificaciones asíncronas para la plataforma de arrendamientos "
        "de bienes raíces en Costa Rica.\n\n"
        "- **Origen de eventos**: Azure Service Bus (consumidor)\n"
        "- **Persistencia**: Azure Cosmos DB (API MongoDB)\n"
        "- **Tiempo real**: WebSockets nativos de FastAPI\n"
        "- **Push móvil**: Firebase Cloud Messaging (FCM)\n"
        "- **Seguridad**: JWT (validado contra microservicio de usuarios, llega vía APIM)"
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "Notificaciones", "description": "Gestión de notificaciones del usuario"},
        {"name": "Dispositivos", "description": "Registro de dispositivos para push notifications"},
        {"name": "WebSocket", "description": "Canal de tiempo real por usuario"},
    ],
)

# ------------------------------------------------------------------
# Middleware CORS
# ------------------------------------------------------------------

cors_origins_env = os.getenv("CORS_ORIGINS", "http://localhost:3000")
cors_origins = [origin.strip() for origin in cors_origins_env.split(",") if origin.strip()]

app.add_middleware(
    CORSMiddleware,
    allow_origins=cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------
# Registrar routers
# ------------------------------------------------------------------

app.include_router(router_notificaciones)
app.include_router(router_dispositivos)
app.include_router(router_websocket)

# ------------------------------------------------------------------
# Health Check
# ------------------------------------------------------------------

@app.get(
    "/health",
    response_model=HealthResponse,
    tags=["Sistema"],
    summary="Health check del servicio",
    description="Verifica el estado del servicio y retorna métricas básicas.",
)
async def health_check() -> HealthResponse:
    """GET /health - Endpoint de verificación de salud."""
    ws_mgr: WebSocketManager | None = getattr(app.state, "ws_manager", None)
    return HealthResponse(
        status="healthy",
        service="notificaciones",
        ws_connections=ws_mgr.conexiones_activas if ws_mgr else 0,
        ws_users=ws_mgr.usuarios_conectados if ws_mgr else 0,
    )
