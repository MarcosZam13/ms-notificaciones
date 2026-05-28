# Microservicio de Notificaciones (`ms-notificaciones`)

Servicio de notificaciones asíncronas para la plataforma de arrendamientos de bienes raíces en Costa Rica. Consume eventos de Azure Service Bus, persiste notificaciones en Azure Cosmos DB y las distribuye en tiempo real vía WebSocket nativo (FastAPI) y Firebase Cloud Messaging (FCM).

**Desplegado en:** `https://ms-notificaciones.azurewebsites.net`  
**Repositorio:** `https://github.com/MarcosZam13/ms-notificaciones`  
**Runtime:** Python 3.11 · FastAPI · Uvicorn · Pydantic v2

---

## Tabla de contenidos

1. [Arquitectura interna](#1-arquitectura-interna)
2. [Endpoints REST](#2-endpoints-rest)
3. [WebSocket nativo](#3-websocket-nativo)
4. [Consumer de Azure Service Bus](#4-consumer-de-azure-service-bus)
5. [Push Notifications (FCM)](#5-push-notifications-fcm)
6. [Modelos de datos](#6-modelos-de-datos)
7. [Flujo completo de una notificación](#7-flujo-completo-de-una-notificación)
8. [Variables de entorno](#8-variables-de-entorno)
9. [Ejecutar localmente](#9-ejecutar-localmente)
10. [CI/CD — GitHub Actions](#10-cicd--github-actions)
11. [Seguridad](#11-seguridad)
12. [Estructura del proyecto](#12-estructura-del-proyecto)

---

## 1. Arquitectura interna

El microservicio sigue **arquitectura hexagonal** (puertos y adaptadores):

```
┌─────────────────────────────────────────────────────────┐
│                 INTERFACES (HTTP REST / WebSocket)       │
│  router_notificaciones  router_dispositivos              │
│  router_websocket       auth (verificar_token)           │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                APPLICATION (servicios)                   │
│  NotificationService   DeviceService                     │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│                    DOMAIN (modelos)                      │
│  EventoServiceBus  Notificacion  NotificacionRespuesta   │
│  DispositivoUsuario  RegistroDispositivoRequest          │
└────────────────────────┬────────────────────────────────┘
                         │
┌────────────────────────▼────────────────────────────────┐
│               INFRASTRUCTURE (adaptadores)               │
│  CosmosDBClient   ServiceBusListener                     │
│  FCMClient        WebSocketManager                       │
└─────────────────────────────────────────────────────────┘
```

**Dependencias externas:**
- **Azure Service Bus** — consumidor (topic `mensajes-eventos`, suscripción `notificaciones-sub`)
- **Azure Cosmos DB (API MongoDB)** — persistencia de notificaciones y tokens FCM
- **WebSocket nativo FastAPI** — canal `/ws/{usuario_id}?token=<JWT>`
- **Firebase Cloud Messaging (FCM)** — push notifications a dispositivos móviles/web

---

## 2. Endpoints REST

Todas las rutas (excepto `/health`) requieren el header:
```
Authorization: Bearer <JWT>
```

El JWT es validado usando:
- **RS256 + JWKS** (recomendado): si `USUARIOS_MS_URL` está definida — descarga la clave pública del MS Usuarios automáticamente
- **HS256 + secret compartido** (fallback): si solo `JWT_SECRET` está definida

---

### `GET /notificaciones/{usuario_id}`

Lista las notificaciones del usuario de forma paginada, ordenadas por fecha descendente.

**Autorización:** El claim `sub` del JWT debe coincidir con `usuario_id` del path (403 si difieren).

**Query params:**

| Param    | Default | Rango | Descripción        |
|----------|---------|-------|--------------------|
| `pagina` | `1`     | ≥ 1   | Número de página   |
| `tamano` | `20`    | 1–100 | Items por página   |

**Respuesta 200:**
```json
{
  "items": [
    {
      "id":         "683c2a4f...",
      "usuario_id": "user-456",
      "tipo":       "nuevo_mensaje",
      "titulo":     "Nuevo mensaje de Carlos",
      "cuerpo":     "Hola, ¿sigue disponible el apartamento?",
      "leida":      false,
      "metadata": {
        "remitente_nombre": "Carlos",
        "propiedad_id":     "prop-123",
        "conversacion_id":  "conv-789"
      },
      "creada_en":  "2026-05-27T14:30:00.000Z"
    }
  ],
  "total":         15,
  "pagina":        1,
  "tamano_pagina": 20,
  "total_paginas": 1
}
```

---

### `PATCH /notificaciones/{notificacion_id}/leer`

Marca una notificación específica como leída.

**Respuesta 200:**
```json
{
  "mensaje": "Notificación marcada como leída",
  "id": "683c2a4f..."
}
```

**Respuesta 404:** Si `notificacion_id` no existe.

---

### `GET /notificaciones/{usuario_id}/no-leidas`

Retorna el conteo de notificaciones sin leer del usuario.

**Autorización:** El claim `sub` del JWT debe coincidir con `usuario_id`.

**Respuesta 200:**
```json
{
  "usuario_id": "user-456",
  "no_leidas":  5
}
```

---

### `POST /dispositivos/dispositivo`

Registra o actualiza el token FCM de un dispositivo para recibir push notifications.

**Autorización:** El claim `sub` del JWT debe coincidir con `body.usuario_id`.

**Body:**
```json
{
  "usuario_id": "user-456",
  "fcm_token":  "dXJ7nK...",
  "plataforma": "android"
}
```

**Respuesta 200:**
```json
{
  "id":             "683d...",
  "usuario_id":     "user-456",
  "fcm_token":      "dXJ7nK...",
  "plataforma":     "android",
  "actualizado_en": "2026-05-27T15:00:00.000Z"
}
```

---

### `GET /health`

Health check sin autenticación. Incluye métricas en tiempo real del WebSocket Manager.

```json
{
  "status":         "healthy",
  "service":        "notificaciones",
  "ws_connections": 12,
  "ws_users":       8
}
```

---

### Códigos de error comunes

| Código | Causa                                              |
|--------|----------------------------------------------------|
| `401`  | Token JWT ausente, expirado o inválido             |
| `403`  | El JWT pertenece a otro usuario (ownership check)  |
| `404`  | Notificación no encontrada                         |
| `422`  | Body inválido (validación Pydantic)                |
| `500`  | Error interno (ver logs de Azure App Service)      |

---

### Documentación interactiva (Swagger)

- **Swagger UI:** `https://ms-notificaciones.azurewebsites.net/docs`
- **ReDoc:** `https://ms-notificaciones.azurewebsites.net/redoc`

---

## 3. WebSocket nativo

El WebSocket usa el soporte nativo de FastAPI (Starlette), no Socket.io.

### Conexión

```
wss://ms-notificaciones.azurewebsites.net/ws/{usuario_id}?token={JWT}
```

El token JWT se pasa como **query parameter** (WebSocket no soporta headers personalizados en el handshake desde el navegador).

**Validaciones al conectar:**
1. Falta `?token=` → `close(4001, "Token JWT requerido en query param '?token='")`
2. Token inválido/expirado → `close(4001, descripción del error)`
3. `token.sub ≠ usuario_id` del path → `close(4003, "El token no pertenece al usuario")`
4. Todo válido → `accept()` + registrar en WebSocketManager

---

### Protocolo de mensajes

| Dirección          | Mensaje  | Descripción                                 |
|--------------------|----------|---------------------------------------------|
| cliente → servidor | `"ping"` | Keepalive — el servidor responde `"pong"`   |
| servidor → cliente | `"pong"` | Respuesta al ping                           |
| servidor → cliente | JSON     | `NotificacionRespuesta` serializada         |

**Payload de notificación:**
```json
{
  "id":         "683c2a4f...",
  "usuario_id": "user-456",
  "tipo":       "nuevo_mensaje",
  "titulo":     "Nuevo mensaje de Carlos",
  "cuerpo":     "Hola, ¿sigue disponible el apartamento?",
  "leida":      false,
  "metadata": {
    "conversacion_id":  "conv-789",
    "propiedad_id":     "prop-123",
    "remitente_nombre": "Carlos"
  },
  "creada_en": "2026-05-27T14:30:00.000Z"
}
```

---

### Ejemplo de conexión (frontend — hook React)

```typescript
// Extracto de useNotificacionesWS.ts
const NOTIF_WS_URL = import.meta.env.VITE_NOTIF_WS_URL;

const ws = new WebSocket(
  `wss://${NOTIF_WS_URL}/ws/${userId}?token=${jwt}`
);

// Ping cada 30s para mantener la conexión viva en Azure
const pingInterval = setInterval(() => {
  if (ws.readyState === WebSocket.OPEN) ws.send('ping');
}, 30_000);

ws.onmessage = ({ data }) => {
  if (data === 'pong') return;                         // ignorar pong
  const notificacion = JSON.parse(data);
  // agregar al estado, mostrar toast, etc.
};

ws.onclose = ({ code }) => {
  if (code === 4001 || code === 4003) return;          // error de auth — NO reconectar
  // reconectar con backoff exponencial (3s → 6s → 12s → ... hasta 60s)
};
```

---

## 4. Consumer de Azure Service Bus

Al iniciar, FastAPI lanza una **tarea asyncio** en background que escucha el topic `mensajes-eventos` en la suscripción `notificaciones-sub`.

### Formato del mensaje (publicado por MS Mensajes)

```json
{
  "tipo":             "nuevo_mensaje",
  "destinatario_id":  "user-456",
  "remitente_nombre": "Carlos López",
  "propiedad_id":     "prop-123",
  "preview":          "Hola, ¿sigue disponible?",
  "conversacion_id":  "conv-789"
}
```

El modelo `EventoServiceBus` (Pydantic) deriva automáticamente:
- `titulo` ← `"Nuevo mensaje de {remitente_nombre}"` (si no viene en el payload)
- `cuerpo` ← `preview` (si no viene en el payload)
- `metadata` ← enriquecido con `remitente_nombre`, `propiedad_id`, `conversacion_id`

### Estrategia de reintentos

| Situación                          | Acción                                            |
|------------------------------------|---------------------------------------------------|
| JSON inválido                      | Dead-letter inmediato (no reintentable)           |
| Error de procesamiento (1.ª–2.ª)  | `abandon_message()` → vuelve a la cola           |
| ≥ 3 entregas fallidas              | Dead-letter con descripción del error             |
| Error de conexión al namespace     | Espera 5 s y reconecta automáticamente           |

---

## 5. Push Notifications (FCM)

### Configuración

1. En Firebase Console: **Configuración del proyecto → Cuentas de servicio → Generar nueva clave privada**
2. Guardar el JSON descargado como `firebase-credentials.json`
3. Configurar la variable: `FCM_CREDENTIALS_PATH=./firebase-credentials.json`

> Si el archivo no existe o `FCM_CREDENTIALS_PATH` no está definida, el servicio arranca con un warning y las push notifications quedan deshabilitadas. Los WebSockets y la persistencia siguen funcionando.

### Flujo de envío

```
1. Usuario abre la app web → POST /dispositivos/dispositivo con su FCM token
2. Al llegar un evento de Service Bus:
   a. NotificationService.procesar_evento(evento)
   b. Persistir en Cosmos DB
   c. Emitir por WebSocket (si el usuario está conectado)
   d. Buscar fcm_token del usuario → firebase_admin.messaging.send(Message(...))
```

---

## 6. Modelos de datos

### Colección `notificaciones`

| Campo        | Tipo     | Descripción                                                    |
|--------------|----------|----------------------------------------------------------------|
| `_id`        | ObjectId | Generado por Cosmos DB                                         |
| `usuario_id` | string   | ID del usuario destinatario                                    |
| `tipo`       | string   | `nuevo_mensaje`, `contrato_firmado`, `pago_recibido`, etc.     |
| `titulo`     | string   | Título breve para mostrar en la UI                             |
| `cuerpo`     | string   | Descripción de la notificación                                 |
| `leida`      | boolean  | `false` hasta que el usuario la marque como leída              |
| `metadata`   | dict     | Datos adicionales según tipo (ej. `conversacion_id`)           |
| `creada_en`  | datetime | Timestamp UTC de creación                                      |

**Índices:**
- `{ usuario_id, creada_en: -1 }` — listado paginado por usuario
- `{ usuario_id, leida }` — conteo eficiente de no leídas

### Colección `dispositivos`

| Campo            | Tipo     | Descripción                               |
|------------------|----------|-------------------------------------------|
| `_id`            | ObjectId | Generado por Cosmos DB                    |
| `usuario_id`     | string   | ID del usuario dueño del dispositivo      |
| `fcm_token`      | string   | Token FCM para push notifications         |
| `plataforma`     | string   | `android`, `ios` o `web`                |
| `actualizado_en` | datetime | Última actualización del token (upsert)   |

---

## 7. Flujo completo de una notificación

```
MS Mensajes
  │── serviceBusPublisher.publicarEvento({ tipo: 'nuevo_mensaje', ... })
  ▼
Azure Service Bus (topic: mensajes-eventos / suscripción: notificaciones-sub)
  ▼
ServiceBusListener._procesar_mensaje()
  │── Decodifica bytes → str → JSON
  │── EventoServiceBus(**data)    ← Pydantic valida y deriva titulo/cuerpo
  │── notification_service.procesar_evento(evento)
  │── receiver.complete_message() ← confirma recepción (elimina de la cola)
  ▼
NotificationService.procesar_evento()
  │
  ├─ 1. cosmos_client.guardar_notificacion({
  │        usuario_id, tipo, titulo, cuerpo, metadata
  │     })                              ← siempre se persiste
  │
  ├─ 2. ws_manager.enviar_a_usuario(usuario_id, notificacion_json)
  │        → Si conectado:  WebSocket.send(json)   ← tiempo real
  │        → Si no conectado: omite (la notif. quedó en BD para cuando conecte)
  │
  └─ 3. fcm_client.enviar_push(usuario_id, titulo, cuerpo, metadata)
           → Busca fcm_token del usuario en `dispositivos`
           → firebase_admin.messaging.send(Message(...))
           → Si FCM deshabilitado: omite silenciosamente
```

---

## 8. Variables de entorno

```env
# ── Azure Cosmos DB (API MongoDB) ────────────────────────
COSMOS_CONNECTION_STRING=mongodb://<cuenta>:<clave>@<cuenta>.mongo.cosmos.azure.com:10255/?ssl=true&replicaSet=globaldb&retrywrites=false&maxIdleTimeMS=120000
COSMOS_DATABASE=notificaciones_db

# ── Azure Service Bus ─────────────────────────────────────
SERVICE_BUS_CONNECTION_STRING=Endpoint=sb://<namespace>.servicebus.windows.net/;SharedAccessKeyName=RootManageSharedAccessKey;SharedAccessKey=<clave>
SERVICE_BUS_TOPIC_NAME=mensajes-eventos
SERVICE_BUS_SUBSCRIPTION_NAME=notificaciones-sub

# ── Firebase Cloud Messaging ──────────────────────────────
FCM_CREDENTIALS_PATH=./firebase-credentials.json

# ── JWT ───────────────────────────────────────────────────
# Opción 1 (recomendada) — RS256 + JWKS del MS Usuarios
# USUARIOS_MS_URL=https://<ms-usuarios>.azurewebsites.net

# Opción 2 (fallback) — HS256 con secreto compartido
JWT_SECRET=mismo-secreto-que-ms-mensajes-y-frontend
JWT_ALGORITHM=HS256

# ── CORS ──────────────────────────────────────────────────
CORS_ORIGINS=https://agreeable-ground-0b1436910.6.azurestaticapps.net,http://localhost:5173

# ── Servidor ─────────────────────────────────────────────
HOST=0.0.0.0
PORT=8000
LOG_LEVEL=INFO
```

| Variable                        | Requerida | Default               | Descripción                                      |
|---------------------------------|-----------|-----------------------|--------------------------------------------------|
| `COSMOS_CONNECTION_STRING`      | ✅        | —                     | Cadena de conexión Cosmos DB (API MongoDB)        |
| `COSMOS_DATABASE`               | ⬜        | `notificaciones_db`   | Nombre de la base de datos                       |
| `SERVICE_BUS_CONNECTION_STRING` | ⬜        | —                     | Si falta, el listener no arranca (warning)       |
| `SERVICE_BUS_TOPIC_NAME`        | ⬜        | `mensajes-eventos`    | Topic de Azure Service Bus                       |
| `SERVICE_BUS_SUBSCRIPTION_NAME` | ⬜        | `notificaciones-sub`  | Suscripción del topic                            |
| `FCM_CREDENTIALS_PATH`          | ⬜        | —                     | Si falta, push notifications deshabilitadas      |
| `JWT_SECRET`                    | ⬜†       | —                     | Requerido si no se usa `USUARIOS_MS_URL`          |
| `USUARIOS_MS_URL`               | ⬜†       | —                     | URL MS Usuarios para JWKS (RS256)                |
| `CORS_ORIGINS`                  | ⬜        | `http://localhost:3000`| Orígenes CORS separados por coma               |
| `PORT`                          | ⬜        | `8000`                | Puerto del servidor                              |

†: Al menos uno de `JWT_SECRET` o `USUARIOS_MS_URL` debe estar definido.

---

## 9. Ejecutar localmente

```bash
# 1. Crear y activar entorno virtual
python -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate

# 2. Instalar dependencias
pip install -r requirements.txt

# 3. Configurar variables de entorno
cp .env.example .env
# Editar .env con los valores reales

# 4. Ejecutar (desarrollo con hot-reload)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

**Endpoints disponibles localmente:**
- API REST + Swagger: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
- Health: `http://localhost:8000/health`
- WebSocket: `ws://localhost:8000/ws/{usuario_id}?token={JWT}`

---

## 10. CI/CD — GitHub Actions

Workflow `.github/workflows/deploy.yml` activado en cada push a `main`:

```
1. Checkout           actions/checkout@v4
2. zip deploy.zip     todos los archivos del proyecto
                      (excluye .git, __pycache__, .pyc, .env*, .log)
3. curl Kudu API      POST https://ms-notificaciones.scm.azurewebsites.net/api/zipdeploy
                      Azure ejecuta automáticamente:
                        pip install -r requirements.txt
                        uvicorn app.main:app (startup command del App Service)
```

**Secrets requeridos en GitHub:**

| Secret                         | Valor                                         |
|--------------------------------|-----------------------------------------------|
| `KUDU_USER_MS_NOTIFICACIONES`  | `$ms-notificaciones` (literal, incluye el `$`) |
| `KUDU_PASS_MS_NOTIFICACIONES`  | Contraseña del perfil de publicación de Azure  |

> **Variables de entorno en producción:** Se configuran en Azure App Service → Configuration → Application Settings. No van en el ZIP.

---

## 11. Seguridad

### Validación JWT con dos modos

`app/interfaces/auth.py` elige el modo según las variables de entorno:

| Variable definida | Modo         | Descripción                                                                 |
|-------------------|--------------|-----------------------------------------------------------------------------|
| `USUARIOS_MS_URL` | RS256 + JWKS | Descarga la clave pública del MS Usuarios. Cache en memoria (1 hora). Recomendado en producción. |
| `JWT_SECRET`      | HS256        | Secreto compartido con MS Mensajes y el frontend. Usado en el proyecto actual. |

### Ownership check en todos los endpoints de usuario

```python
# Patrón aplicado en listar_notificaciones, conteo_no_leidas y registrar_dispositivo
if payload.get("sub") != usuario_id:
    raise HTTPException(403, "No autorizado para acceder a recursos de otro usuario")
```

### Códigos de cierre WebSocket semánticos

| Código | Causa                               | El cliente debe reconectar |
|--------|-------------------------------------|---------------------------|
| `4001` | Token faltante o inválido           | No                        |
| `4003` | Token pertenece a otro usuario      | No                        |
| `1000` | Cierre normal                       | Sí (con backoff)          |

### Graceful degradation

- Si Service Bus no está configurado → el microservicio arranca normalmente, sin listener
- Si FCM no está configurado → el microservicio arranca normalmente, sin push notifications
- Solo `COSMOS_CONNECTION_STRING` es requerido al inicio; su ausencia lanza `RuntimeError`

---

## 12. Estructura del proyecto

```
ms-notificaciones/
├── app/
│   ├── __init__.py
│   ├── main.py                          # Bootstrap: lifespan, CORS, routers, /health
│   ├── domain/
│   │   ├── __init__.py
│   │   └── models.py                    # Pydantic v2: EventoServiceBus, Notificacion,
│   │                                    # NotificacionRespuesta, DispositivoUsuario, etc.
│   ├── application/
│   │   ├── __init__.py
│   │   └── services.py                  # NotificationService, DeviceService
│   ├── infrastructure/
│   │   ├── __init__.py
│   │   ├── database.py                  # CosmosDBClient — Motor async
│   │   ├── service_bus.py               # ServiceBusListener — consumer asíncrono en background
│   │   ├── fcm_client.py                # FCMClient — Firebase Admin SDK
│   │   └── websocket_manager.py         # WebSocketManager — gestión de conexiones por usuario
│   └── interfaces/
│       ├── __init__.py
│       ├── auth.py                      # verificar_token (HTTP) + verificar_token_ws (WS)
│       └── routes.py                    # FastAPI routers + WebSocket endpoint
├── .github/
│   └── workflows/
│       └── deploy.yml                   # CI/CD: zip + Kudu ZIP deploy
├── .env.example                         # Plantilla de variables de entorno
├── requirements.txt                     # Dependencias Python
├── Dockerfile                           # Para despliegue en contenedor (opcional)
├── postman_collection.json              # Colección Postman con todos los endpoints
└── README.md
```
