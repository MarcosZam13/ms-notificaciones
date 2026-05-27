# Microservicio de Notificaciones

Servicio de notificaciones asíncronas para la plataforma de arrendamientos de bienes raíces en Costa Rica. Consume eventos de Azure Service Bus, persiste notificaciones en Cosmos DB y las distribuye en tiempo real por WebSocket (web) y Firebase Cloud Messaging (móvil).

**Desplegado en:** `https://ms-notificaciones.azurewebsites.net`
**Swagger UI:** `https://ms-notificaciones.azurewebsites.net/docs`

---

## Stack Tecnológico

| Componente        | Tecnología                            |
|-------------------|---------------------------------------|
| Lenguaje          | Python 3.11+                          |
| Framework         | FastAPI                               |
| Base de datos     | Azure Cosmos DB (API MongoDB)         |
| Driver DB         | Motor (async MongoDB)                 |
| Cola de mensajes  | Azure Service Bus (Topic + Suscripción) |
| Tiempo real web   | WebSockets nativos de FastAPI         |
| Push móvil        | Firebase Cloud Messaging (FCM)        |
| Autenticación     | JWT HS256 o RS256+JWKS (configurable) |
| Documentación     | Swagger UI auto-generado por FastAPI  |
| Despliegue        | Azure App Service — Oryx build (Python) |
| CI/CD             | GitHub Actions                        |

---

## Arquitectura

```
┌─────────────────────────────────────────────────────────────────┐
│                    Azure API Management (APIM)                   │
│  Autentica, autoriza y enruta peticiones HTTP + WebSocket       │
└──────────────────┬──────────────────────────────────────────────┘
                   │
┌──────────────────▼──────────────────────────────────────────────┐
│              Microservicio de Notificaciones                     │
│                  (Azure App Service — Python 3.11)               │
│                                                                  │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────────────┐     │
│  │  FastAPI     │  │ Service Bus  │  │ Firebase Cloud     │     │
│  │  REST + WS   │  │ Listener     │  │ Messaging (FCM)    │     │
│  └──────┬───────┘  └──────┬───────┘  └────────┬───────────┘     │
│         │                 │                    │                 │
│  ┌──────▼─────────────────▼────────────────────▼───────────┐    │
│  │               Azure Cosmos DB (MongoDB API)              │    │
│  │        notificaciones_db.notificaciones                  │    │
│  │        notificaciones_db.dispositivos                    │    │
│  └──────────────────────────────────────────────────────────┘    │
└──────────────────────────────────────────────────────────────────┘
         ▲                    ▲                    ▲
    ┌────┴────┐         ┌────┴─────────┐    ┌─────┴─────┐
    │ React   │         │ MS Mensajes  │    │ Android / │
    │ Web App │         │ (publica en  │    │ iOS App   │
    │(WebSocket│        │ Service Bus) │    │(Push FCM) │
    └─────────┘         └─────────────┘    └───────────┘
```

**Flujo de una notificación:**
1. MS Mensajes publica un evento en Azure Service Bus (topic `mensajes-eventos`)
2. Este MS lo recibe vía suscripción `notificaciones-sub`
3. Persiste la notificación en Cosmos DB (`leida: false`)
4. Emite en tiempo real por WebSocket al usuario destinatario
5. Envía push notification vía FCM al dispositivo móvil

Los pasos 4 y 5 son fire-and-forget: si el usuario no está conectado por WS o no tiene FCM registrado, la notificación queda persistida para consulta posterior.

---

## Estructura del proyecto

```
app/
├── domain/
│   └── models.py              # Modelos Pydantic (entidades y validación)
├── application/
│   └── services.py            # Casos de uso: NotificationService, DeviceService
├── infrastructure/
│   ├── database.py            # Cliente Cosmos DB async (Motor), índices, TTL
│   ├── service_bus.py         # Listener de Azure Service Bus (background task)
│   ├── fcm_client.py          # Cliente Firebase Cloud Messaging
│   └── websocket_manager.py   # Gestor de conexiones WebSocket
├── interfaces/
│   ├── routes.py              # Routers REST y WebSocket de FastAPI
│   └── auth.py                # Validación JWT (HS256 o RS256+JWKS)
└── main.py                    # Punto de entrada FastAPI — lifespan, montaje de routers
```

---

## Variables de entorno

Copia `.env.example` a `.env` y configura los valores:

```bash
cp .env.example .env
```

| Variable                        | Requerida | Descripción                                                                    |
|---------------------------------|-----------|--------------------------------------------------------------------------------|
| `COSMOS_CONNECTION_STRING`      | **Sí**    | Connection string a Azure Cosmos DB (API MongoDB)                              |
| `COSMOS_DATABASE`               | No        | Nombre de la base de datos (default: `notificaciones_db`)                      |
| `SERVICE_BUS_CONNECTION_STRING` | No        | Connection string de `arrendamientos-sb1`. Si está vacía, el listener no inicia. ✅ Configurada en producción |
| `SERVICE_BUS_TOPIC_NAME`        | No        | Nombre del topic (default: `mensajes-eventos`)                                 |
| `SERVICE_BUS_SUBSCRIPTION_NAME` | No        | Nombre de la suscripción (default: `notificaciones-sub`)                       |
| `FCM_CREDENTIALS_PATH`          | No        | Ruta al JSON de credenciales de Firebase. Si está vacío, FCM se deshabilita.  |
| `JWT_SECRET`                    | No*       | Secret HS256. Requerido si `USUARIOS_MS_URL` no está definida.                 |
| `USUARIOS_MS_URL`               | No*       | URL del MS Usuarios para descargar JWKS (RS256). Tiene prioridad sobre `JWT_SECRET`. |
| `JWT_AUDIENCE`                  | No        | Audiencia esperada del token (opcional)                                        |
| `CORS_ORIGINS`                  | No        | Orígenes CORS separados por coma (default: `http://localhost:3000,http://localhost:5173`) |
| `LOG_LEVEL`                     | No        | Nivel de logging (default: `INFO`)                                             |
| `HOST`                          | No        | Host del servidor (default: `0.0.0.0`)                                         |
| `PORT`                          | No        | Puerto (default: `8000`)                                                       |

*Al menos una de `JWT_SECRET` o `USUARIOS_MS_URL` debe estar definida para la validación JWT.

---

## Instalación y ejecución local

```bash
# 1. Crear entorno virtual
python -m venv .venv
source .venv/bin/activate  # Linux/Mac

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales

# 4. Ejecutar con hot reload
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

El servicio estará disponible en:
- API: http://localhost:8000
- Swagger UI: http://localhost:8000/docs
- ReDoc: http://localhost:8000/redoc
- Health check: http://localhost:8000/health

---

## Endpoints API

Todos los endpoints HTTP requieren `Authorization: Bearer <JWT>`.
El WebSocket requiere el token como query param: `?token=<JWT>`.

| Método  | Ruta                                         | Auth | Descripción                              |
|---------|----------------------------------------------|------|------------------------------------------|
| `GET`   | `/health`                                    | No   | Health check del servicio                |
| `GET`   | `/docs`                                      | No   | Swagger UI (auto-generado por FastAPI)   |
| `GET`   | `/notificaciones/{usuario_id}`               | Sí   | Listar notificaciones (paginado)         |
| `GET`   | `/notificaciones/{usuario_id}/no-leidas`     | Sí   | Conteo de notificaciones no leídas       |
| `PATCH` | `/notificaciones/{notificacion_id}/leer`     | Sí   | Marcar notificación como leída           |
| `POST`  | `/dispositivos/dispositivo`                  | Sí   | Registrar o actualizar token FCM         |
| `WS`    | `/ws/{usuario_id}?token=<JWT>`              | Sí   | Canal WebSocket de tiempo real           |

### Paginación

`GET /notificaciones/{usuario_id}?pagina=1&tamano=20`

| Parámetro | Default | Rango  | Descripción          |
|-----------|---------|--------|----------------------|
| `pagina`  | 1       | >= 1   | Número de página     |
| `tamano`  | 20      | 1-100  | Items por página     |

---

## WebSocket

```javascript
const ws = new WebSocket(
  'wss://ms-notificaciones.azurewebsites.net/ws/usuario-123?token=<JWT>'
);

ws.onmessage = (event) => {
  const notificacion = JSON.parse(event.data);
  console.log('Notificación en tiempo real:', notificacion);
};

// Ping para mantener la conexión activa
setInterval(() => ws.send('ping'), 30000);
```

---

## Registro de token FCM

### Desde el frontend web (React)

```javascript
import { getMessaging, getToken } from "firebase/messaging";

const messaging = getMessaging();
const fcmToken = await getToken(messaging, { vapidKey: "TU_VAPID_KEY" });

await fetch("https://ms-notificaciones.azurewebsites.net/dispositivos/dispositivo", {
  method: "POST",
  headers: {
    "Authorization": "Bearer <JWT>",
    "Content-Type": "application/json"
  },
  body: JSON.stringify({
    usuario_id: "id-del-usuario",
    fcm_token: fcmToken,
    plataforma: "web"
  })
});
```

### Desde app Android (Kotlin)

```kotlin
class AppFirebaseService : FirebaseMessagingService() {
    override fun onNewToken(token: String) {
        CoroutineScope(Dispatchers.IO).launch {
            val client = HttpClient()
            client.post("https://ms-notificaciones.azurewebsites.net/dispositivos/dispositivo") {
                header("Authorization", "Bearer $jwt")
                contentType(ContentType.Application.Json)
                setBody("""{"usuario_id":"$userId","fcm_token":"$token","plataforma":"android"}""")
            }
        }
    }
}
```

---

## Formato de eventos de Service Bus

El MS Mensajes publica en el topic `mensajes-eventos`. Este servicio está suscrito vía `notificaciones-sub`:

```json
{
  "tipo": "nuevo_mensaje",
  "destinatario_id": "user-456",
  "remitente_nombre": "Carlos Pérez",
  "propiedad_id": "prop-789",
  "preview": "Hola, me interesa la propiedad...",
  "conversacion_id": "66a1f3c2e4b09d2e1a3f8fff"
}
```

El listener implementa manejo robusto de errores:
- Procesamiento exitoso → `complete()` (mensaje eliminado del bus)
- JSON inválido → `dead_letter()` (cola de mensajes fallidos para revisión)
- Error temporal → `abandon()` (Azure reintenta automáticamente hasta 3 veces)
- Caída de conexión → reconexión automática con espera de 5 segundos

---

## Persistencia en Cosmos DB

Las notificaciones se guardan con índices optimizados:

```json
{
  "_id": "ObjectId generado",
  "usuario_id": "user-456",
  "tipo": "nuevo_mensaje",
  "titulo": "Nuevo mensaje de Carlos Pérez",
  "cuerpo": "Hola, me interesa la propiedad...",
  "metadata": { "conversacion_id": "...", "propiedad_id": "..." },
  "leida": false,
  "creada_en": "2026-05-20T22:00:00Z"
}
```

Índices creados al iniciar:
- `usuario_id` — para listar notificaciones
- `(usuario_id, creada_en)` — para ordenar por fecha
- `(usuario_id, leida)` — para contar no leídas
- TTL de 90 días en `creada_en` — limpieza automática

---

## Despliegue en Azure App Service

El servicio usa **Oryx build** (Azure compila Python en el servidor). Solo se sube el código fuente y `requirements.txt`.

### Variables de entorno en producción

Configuradas en Azure Portal → `ms-notificaciones` → Configuration:

| Variable | Valor en producción |
|---|---|
| `COSMOS_CONNECTION_STRING` | Connection string de `mongoclusterjoseph` |
| `COSMOS_DATABASE` | `notificaciones_db` |
| `JWT_SECRET` | `secret_seguro_aqui_123456789` |
| `SERVICE_BUS_TOPIC_NAME` | `mensajes-eventos` |
| `SERVICE_BUS_SUBSCRIPTION_NAME` | `notificaciones-sub` |
| `CORS_ORIGINS` | URL del frontend estático |
| `WEBSITES_PORT` | `8000` |
| `SCM_DO_BUILD_DURING_DEPLOYMENT` | `true` |
| `SERVICE_BUS_CONNECTION_STRING` | `Endpoint=sb://arrendamientos-sb1.servicebus.windows.net/;...` ✅ Configurada |
| `FCM_CREDENTIALS_PATH` | Pendiente — configurar credenciales Firebase (ver sección FCM) |

El startup command configurado es: `uvicorn app.main:app --host 0.0.0.0 --port 8000`

---

## GitHub Actions CI/CD

El workflow en `.github/workflows/deploy.yml` se activa al hacer push a `main`.

**Pasos del pipeline:**
1. `pip install -r requirements.txt` — instala dependencias
2. `ruff check` — linting de código Python
3. `pytest` — ejecuta pruebas (si las hay)
4. ZIP deploy del código fuente a Azure App Service (Oryx compila en el servidor)

### Secrets requeridos en GitHub

| Secret | Descripción |
| ------ | ----------- |
| `AZURE_WEBAPP_PUBLISH_PROFILE` | Publish profile del App Service `ms-notificaciones` (descargar desde Azure Portal → ms-notificaciones → Get publish profile) |
| `FCM_CREDENTIALS_JSON` | Contenido del JSON de credenciales de Firebase (cuando esté disponible) |

> **Pendiente:** actualizar el workflow de Docker/ACR a ZIP deploy. Ver ROADMAP.md para el YAML actualizado.

---

## Configuración de Firebase FCM

1. Ir a [Firebase Console](https://console.firebase.google.com/)
2. Crear o seleccionar el proyecto de la app móvil
3. Configuración del proyecto → Cuentas de servicio → Generar nueva clave privada
4. Guardar el archivo JSON descargado
5. En Azure Portal → `ms-notificaciones` → Configuration:
   - Agregar variable `FCM_CREDENTIALS_PATH` = `/home/site/wwwroot/firebase-credentials.json`
6. Subir el archivo JSON al App Service (vía Kudu o incluirlo en el ZIP de deploy)

> El archivo `firebase-credentials.json` **nunca** debe commitearse al repositorio (ya está en `.gitignore`).
