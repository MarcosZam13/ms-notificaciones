"""
Cliente de base de datos para Azure Cosmos DB (API MongoDB).
Proporciona operaciones asíncronas sobre las colecciones de notificaciones y dispositivos.
"""
import logging
from typing import Any, Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase, AsyncIOMotorCollection
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.domain.models import Notificacion, DispositivoUsuario

logger = logging.getLogger(__name__)


class CosmosDBClient:
    """
    Cliente asíncrono para Cosmos DB usando la API de MongoDB.
    Encapsula las operaciones CRUD sobre las colecciones del servicio.
    """

    def __init__(self, connection_string: str, database_name: str) -> None:
        """
        Inicializa la conexión a Cosmos DB.

        Args:
            connection_string: Cadena de conexión MongoDB (formato Cosmos DB).
            database_name: Nombre de la base de datos.
        """
        self._client: AsyncIOMotorClient = AsyncIOMotorClient(
            connection_string,
            serverSelectionTimeoutMS=5000,   # Timeout de selección de servidor
            connectTimeoutMS=10000,          # Timeout de conexión inicial
            maxIdleTimeMS=120000,            # Tiempo máximo de inactividad
        )
        self._database: AsyncIOMotorDatabase = self._client[database_name]

        # Colecciones
        self._notificaciones: AsyncIOMotorCollection = self._database["notificaciones"]
        self._dispositivos: AsyncIOMotorCollection = self._database["dispositivos"]

        logger.info("Cliente Cosmos DB inicializado: base de datos=%s", database_name)

    # ------------------------------------------------------------------
    # Índices
    # ------------------------------------------------------------------

    async def crear_indices(self) -> None:
        """
        Crea los índices necesarios para optimizar las consultas.
        Se ejecuta al iniciar la aplicación. Es idempotente (ignora índices existentes).
        """
        # Índices para la colección de notificaciones
        await self._notificaciones.create_index("usuario_id")
        await self._notificaciones.create_index([("usuario_id", 1), ("creada_en", -1)])
        await self._notificaciones.create_index([("usuario_id", 1), ("leida", 1)])
        # TTL index opcional: eliminar notificaciones automáticamente después de 90 días
        await self._notificaciones.create_index(
            [("creada_en", 1)],
            expireAfterSeconds=7776000,  # 90 días
            name="ttl_creada_en",
        )

        # Índices para la colección de dispositivos
        await self._dispositivos.create_index("usuario_id", unique=True)
        await self._dispositivos.create_index("fcm_token")

        logger.info("Índices de Cosmos DB verificados/creados exitosamente")

    # ------------------------------------------------------------------
    # Operaciones sobre Notificaciones
    # ------------------------------------------------------------------

    async def insertar_notificacion(self, notificacion: Notificacion) -> ObjectId:
        """
        Inserta una nueva notificación en la base de datos.

        Args:
            notificacion: Modelo de notificación a persistir.

        Returns:
            ObjectId generado por MongoDB/Cosmos DB.
        """
        doc = notificacion.model_dump(by_alias=True, exclude={"id"})
        result = await self._notificaciones.insert_one(doc)
        return result.inserted_id

    async def obtener_notificaciones(
        self, usuario_id: str, skip: int = 0, limit: int = 20
    ) -> tuple[list[dict[str, Any]], int]:
        """
        Obtiene notificaciones paginadas de un usuario, ordenadas por fecha descendente.

        Args:
            usuario_id: ID del usuario.
            skip: Cantidad de documentos a saltar (para paginación).
            limit: Máximo de documentos a retornar.

        Returns:
            Tupla (lista_de_documentos, total_de_documentos).
        """
        filtro = {"usuario_id": usuario_id}

        # Ejecutar conteo y consulta en paralelo
        total = await self._notificaciones.count_documents(filtro)

        cursor = (
            self._notificaciones.find(filtro)
            .sort("creada_en", -1)
            .skip(skip)
            .limit(limit)
        )

        notificaciones: list[dict[str, Any]] = []
        async for doc in cursor:
            doc["id"] = str(doc["_id"])  # Convertir ObjectId a string
            del doc["_id"]
            notificaciones.append(doc)

        return notificaciones, total

    async def marcar_leida(self, notificacion_id: str) -> bool:
        """
        Marca una notificación como leída.

        Args:
            notificacion_id: ID de la notificación (string).

        Returns:
            True si se modificó al menos un documento, False en caso contrario.
        """
        try:
            result = await self._notificaciones.update_one(
                {"_id": ObjectId(notificacion_id)},
                {"$set": {"leida": True}},
            )
            return result.modified_count > 0
        except Exception:
            # Si el ID no es un ObjectId válido, no existe
            return False

    async def contar_no_leidas(self, usuario_id: str) -> int:
        """
        Cuenta las notificaciones no leídas de un usuario.

        Args:
            usuario_id: ID del usuario.

        Returns:
            Cantidad de notificaciones sin leer.
        """
        return await self._notificaciones.count_documents({
            "usuario_id": usuario_id,
            "leida": False,
        })

    # ------------------------------------------------------------------
    # Operaciones sobre Dispositivos
    # ------------------------------------------------------------------

    async def upsert_dispositivo(self, dispositivo: DispositivoUsuario) -> None:
        """
        Inserta o actualiza el registro de un dispositivo.
        Si ya existe un registro para el usuario_id, lo actualiza.

        Args:
            dispositivo: Modelo del dispositivo a registrar.
        """
        doc = dispositivo.model_dump(by_alias=True, exclude={"id"})
        await self._dispositivos.update_one(
            {"usuario_id": dispositivo.usuario_id},
            {"$set": doc},
            upsert=True,
        )

    async def obtener_dispositivo(self, usuario_id: str) -> Optional[dict[str, Any]]:
        """
        Obtiene el dispositivo registrado de un usuario.

        Args:
            usuario_id: ID del usuario.

        Returns:
            Documento del dispositivo o None si no existe.
        """
        return await self._dispositivos.find_one({"usuario_id": usuario_id})

    async def obtener_todos_dispositivos(self) -> list[dict[str, Any]]:
        """
        Obtiene todos los dispositivos registrados (para broadcast).

        Returns:
            Lista de documentos de dispositivos.
        """
        dispositivos: list[dict[str, Any]] = []
        async for doc in self._dispositivos.find({}):
            dispositivos.append(doc)
        return dispositivos

    # ------------------------------------------------------------------
    # Ciclo de vida
    # ------------------------------------------------------------------

    async def verificar_conexion(self) -> bool:
        """
        Verifica que la conexión a la base de datos esté activa.

        Returns:
            True si la conexión está activa.
        """
        try:
            await self._client.admin.command("ping")
            return True
        except Exception:
            return False

    async def cerrar(self) -> None:
        """Cierra la conexión a la base de datos de forma ordenada."""
        self._client.close()
        logger.info("Conexión a Cosmos DB cerrada")
