"""
Modelos de dominio y esquemas de datos.
Define las entidades del negocio usando Pydantic v2 para validación y serialización.
"""
from datetime import datetime, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


# ============================================================
# Modelos de entrada (desde Service Bus)
# ============================================================

class EventoServiceBus(BaseModel):
    """
    Modelo del mensaje recibido desde Azure Service Bus.
    Compatible con el formato que publica MS Mensajes (remitente_nombre, preview,
    propiedad_id, conversacion_id) y con formatos genéricos que ya incluyen
    titulo/cuerpo directamente.
    """
    tipo: str = Field(..., description="Tipo de evento: nuevo_mensaje, contrato_firmado, pago_recibido, etc.")
    destinatario_id: str = Field(..., description="ID del usuario destinatario de la notificación")

    # Campos de texto (pueden venir directamente o derivarse de los campos del evento)
    titulo: Optional[str] = Field(default=None, description="Título breve de la notificación")
    cuerpo: Optional[str] = Field(default=None, description="Cuerpo descriptivo de la notificación")

    # Campos específicos de nuevo_mensaje (publicados por MS Mensajes)
    remitente_nombre: Optional[str] = Field(default=None, description="Nombre del remitente del mensaje")
    propiedad_id: Optional[str] = Field(default=None, description="ID de la propiedad relacionada")
    preview: Optional[str] = Field(default=None, description="Vista previa del contenido del mensaje")
    conversacion_id: Optional[str] = Field(default=None, description="ID de la conversación")

    # Metadatos adicionales
    metadata: dict[str, Any] = Field(default_factory=dict, description="Datos adicionales según el tipo de evento")

    @model_validator(mode="after")
    def _derivar_titulo_y_cuerpo(self) -> "EventoServiceBus":
        """
        Si titulo/cuerpo no vienen en el payload (e.g. formato de MS Mensajes),
        se derivan automáticamente de los campos disponibles.
        """
        if self.titulo is None:
            if self.tipo == "nuevo_mensaje" and self.remitente_nombre:
                self.titulo = f"Nuevo mensaje de {self.remitente_nombre}"
            else:
                self.titulo = self.tipo.replace("_", " ").capitalize()

        if self.cuerpo is None:
            self.cuerpo = self.preview or ""

        # Enriquecer metadata con los campos contextuales del evento
        for campo, valor in [
            ("remitente_nombre", self.remitente_nombre),
            ("propiedad_id", self.propiedad_id),
            ("conversacion_id", self.conversacion_id),
        ]:
            if valor and campo not in self.metadata:
                self.metadata[campo] = valor

        return self


# ============================================================
# Modelos de persistencia (Cosmos DB / MongoDB)
# ============================================================

class Notificacion(BaseModel):
    """
    Modelo interno de notificación para persistencia en Cosmos DB.
    El campo _id es generado por MongoDB/Cosmos DB al insertar.
    """
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id", description="ID autogenerado por Cosmos DB")
    usuario_id: str = Field(..., description="ID del usuario propietario de la notificación")
    tipo: str = Field(..., description="Tipo de notificación")
    titulo: str = Field(..., description="Título de la notificación")
    cuerpo: str = Field(..., description="Cuerpo textual de la notificación")
    leida: bool = Field(default=False, description="Indica si el usuario ya leyó la notificación")
    metadata: dict[str, Any] = Field(default_factory=dict, description="Metadatos adicionales del evento")
    creada_en: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Fecha y hora de creación (UTC)"
    )


class NotificacionRespuesta(BaseModel):
    """
    Modelo de respuesta para la API HTTP.
    Serializado limpio sin alias del _id para el cliente.
    """
    id: str = Field(..., description="ID único de la notificación")
    usuario_id: str
    tipo: str
    titulo: str
    cuerpo: str
    leida: bool
    metadata: dict[str, Any]
    creada_en: datetime


class DispositivoUsuario(BaseModel):
    """
    Modelo de registro de un dispositivo móvil/web para recibir push notifications.
    Almacena el token FCM asociado a un usuario.
    """
    model_config = ConfigDict(populate_by_name=True)

    id: Optional[str] = Field(default=None, alias="_id")
    usuario_id: str = Field(..., description="ID del usuario dueño del dispositivo")
    fcm_token: str = Field(..., description="Token FCM del dispositivo para enviar push notifications")
    plataforma: str = Field(default="android", description='Plataforma: "android", "ios" o "web"')
    actualizado_en: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc),
        description="Última actualización del registro"
    )


# ============================================================
# Modelos de petición y respuesta HTTP
# ============================================================

class RegistroDispositivoRequest(BaseModel):
    """Solicitud para registrar o actualizar el token FCM de un dispositivo."""
    usuario_id: str = Field(..., description="ID del usuario")
    fcm_token: str = Field(..., description="Token FCM del dispositivo")
    plataforma: str = Field(default="android", description='Plataforma: "android", "ios" o "web"')


class NotificacionesPaginadas(BaseModel):
    """Respuesta paginada del listado de notificaciones."""
    items: list[NotificacionRespuesta]
    total: int = Field(..., description="Número total de notificaciones")
    pagina: int = Field(..., description="Página actual")
    tamano_pagina: int = Field(..., description="Cantidad de items por página")
    total_paginas: int = Field(..., description="Número total de páginas")


class ConteoNoLeidas(BaseModel):
    """Conteo de notificaciones sin leer de un usuario."""
    usuario_id: str
    no_leidas: int


class MarcarLeidaRespuesta(BaseModel):
    """Respuesta al marcar una notificación como leída."""
    mensaje: str
    id: str


class HealthResponse(BaseModel):
    """Respuesta del endpoint de health check."""
    status: str
    service: str
    ws_connections: int
    ws_users: int
