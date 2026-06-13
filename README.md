# 🔭 Observability Lab: FastAPI + OpenTelemetry Stack

Proyecto de aprendizaje progresivo de observabilidad construido sobre una API
de pagos en FastAPI y desplegado en local con Docker.

---

## Índice

1. [Objetivo](#1-objetivo)
2. [Conceptos fundamentales de observabilidad](#2-conceptos-fundamentales)
3. [Arquitectura final](#3-arquitectura-final)
4. [Requisitos del sistema](#4-requisitos-del-sistema)
5. [Estructura del proyecto](#5-estructura-del-proyecto)
6. [Itinerario de fases](#6-itinerario-de-fases)
   - [Fase 1: La API](#-fase-1-la-api)
   - [Fase 2: Métricas con Prometheus y Grafana](#-fase-2-métricas-con-prometheus-y-grafana)
   - [Fase 3: Logs estructurados con Loki](#-fase-3-logs-estructurados-con-loki)
   - [Fase 4: Trazas distribuidas con Tempo y OpenTelemetry](#-fase-4-trazas-distribuidas-con-tempo-y-opentelemetry)
   - [Fase 5: OTEL Collector, el router central](#-fase-5-otel-collector-el-router-central)
   - [Fase 6: Retención larga con Thanos y MinIO](#-fase-6-retención-larga-con-thanos-y-minio)
   - [Fase 7: Storage persistente para Loki y Tempo en MinIO](#-fase-7-storage-persistente-para-loki-y-tempo-en-minio)
   - [Fase 8: Alertas con Prometheus Alertmanager](#-fase-8-alertas-con-prometheus-alertmanager)
   - [Fase 9: Segunda instancia de Prometheus y deduplicación real con Thanos](#-fase-9-segunda-instancia-de-prometheus-y-deduplicación-real-con-thanos)
   - [Fase 10: Segundo servicio y trazas distribuidas](#-fase-10-segundo-servicio-y-trazas-distribuidas)
7. [Correlación entre las tres señales](#7-correlacion-entre-las-tres-senales)
8. [Métricas expuestas por la API](#8-metricas-expuestas-por-la-api)
9. [Campos de log](#9-campos-de-log)
10. [Notas importantes](#10-notas-importantes)
11. [Dependabot](#11-dependabot)
12. [Validaciones del stack](#12-validaciones)
13. [Errores conceptuales comunes (gotchas)](#13-gotchas)
14. [Preguntas de entrevista](#14-preguntas-entrevista)
15. [Glosario](#15-glosario)
16. [Referencias](#16-referencias)

---

<a name="1-objetivo"></a>
## 🎯 1. Objetivo

Aprender las tecnologías del stack de observabilidad de producción de forma incremental,
fase a fase, con una aplicación real como hilo conductor.

El punto de partida es una API de pagos construida en FastAPI. Sobre ella iremos
añadiendo, capa a capa, todas las herramientas de observabilidad: métricas, logs,
trazas, retención histórica y visualización.

---

---

<a name="2-conceptos-fundamentales"></a>
## 🧠 2. Conceptos fundamentales de observabilidad

Antes de las herramientas, las ideas. Esta sección es la base teórica que
sostiene todo lo que se construye en las fases. Si entiendes esto, las
herramientas concretas (Prometheus, Loki, Tempo) son solo implementaciones
intercambiables de los mismos conceptos.

### Monitorización vs Observabilidad

No son lo mismo aunque se usen como sinónimos.

**Monitorización** responde a preguntas que sabías de antemano que ibas a
hacer: "¿está la CPU por encima del 80%?", "¿responde el endpoint /health?".
Defines dashboards y alertas sobre fallos conocidos.

**Observabilidad** es la capacidad de hacer preguntas que no anticipaste, sin
desplegar código nuevo. "¿Por qué este pago concreto de este cliente tardó
3 segundos un martes a las 14:32?". Requiere datos suficientemente ricos
(alta cardinalidad, contexto, correlación) para investigar lo desconocido.

La diferencia práctica: la monitorización te dice QUE algo falla, la
observabilidad te ayuda a entender POR QUÉ.

### Los tres pilares (the three pillars)

| Pilar | Pregunta que responde | Herramienta aquí | Naturaleza |
|---|---|---|---|
| **Métricas** | ¿Cuánto? ¿Con qué frecuencia? ¿Cuán rápido? | Prometheus | Números agregados en el tiempo, baratos de almacenar |
| **Logs** | ¿Qué pasó exactamente en este evento? | Loki | Eventos discretos con detalle, caros si hay volumen |
| **Trazas** | ¿Por dónde pasó esta petición y cuánto tardó en cada salto? | Tempo | El camino de una petición a través de servicios |

La clave no es tener los tres por separado, sino **correlacionarlos**. Una
métrica te avisa de latencia alta, el `exemplar` de esa métrica te lleva a una
traza concreta, la traza tiene un `trace_id` que te lleva a los logs exactos
de esa petición. Eso es lo que se monta en la [sección de correlación](#7-correlacion-entre-las-tres-senales).

### Los signals de OpenTelemetry

OpenTelemetry (OTel) es el estándar vendor-neutral que unifica cómo se generan
y exportan los datos de telemetría. Define varios "signals":

- **Traces**: trazas distribuidas (estable)
- **Metrics**: métricas (estable)
- **Logs**: logs (estable, con bridge desde librerías existentes como structlog)
- **Profiles**: profiling continuo de CPU/memoria (más reciente)

La promesa de OTel: instrumentas tu código una sola vez con el SDK de OTel, y
puedes cambiar el backend (Tempo por Jaeger, Prometheus por Mimir) sin tocar
la app. Solo cambias la config del Collector. Este proyecto lo demuestra en la
Fase 5.

### Pull vs Push

Dos modelos opuestos de recolección de métricas:

**Pull (Prometheus)**: el servidor va a buscar las métricas scrapeando un
endpoint `/metrics` de cada target cada X segundos. Ventajas: el servidor
controla el ritmo, detecta fácilmente si un target está caído (`up=0`),
configuración centralizada. Es el modelo de Prometheus, Node Exporter, etc.

**Push (OTLP)**: la app envía activamente sus datos a un Collector. Ventajas:
funciona con trabajos efímeros (batch jobs que mueren antes de ser scrapeados),
no requiere que el servidor alcance a la app por red. Es el modelo de OTel,
StatsD, etc.

Este proyecto usa **ambos**: la app empuja métricas via OTLP al Collector
(push), pero Prometheus scrapea los exporters de infraestructura directamente
(pull). La razón de no unificar todo en push está documentada en la Fase 5:
el pipeline `prometheus receiver -> prometheusremotewrite` rompe la semántica
de los counters y `rate()` deja de funcionar.

### Cardinalidad: el concepto que más cuesta dinero

La **cardinalidad** es el número de series temporales únicas que genera una
métrica. Cada combinación distinta de labels crea una serie nueva.

```
http_requests_total{method="GET", status="200"}   <- serie 1
http_requests_total{method="GET", status="404"}   <- serie 2
http_requests_total{method="POST", status="200"}  <- serie 3
```

Con 4 métodos x 5 status = 20 series. Manejable. Pero si añades un label de
alta cardinalidad como `user_id`:

```
http_requests_total{method="GET", status="200", user_id="..."}
```

Con 1 millón de usuarios, esa métrica genera 4 x 5 x 1.000.000 = 20 millones
de series. Eso revienta Prometheus en memoria y disco.

**Regla de oro**: los labels deben tener cardinalidad acotada y baja
(método, status, región, tipo). Nunca uses como label: user_id, email, IP,
trace_id, timestamp, request_id. Esos detalles de alta cardinalidad van en
**logs o trazas**, no en métricas. Confundir esto es el error más caro y común
en observabilidad.

### RED y USE: dos métodos para saber qué medir

No midas todo porque sí. Hay dos frameworks que te dicen qué métricas importan.

**RED method** (para servicios, lo que pide un usuario):
- **R**ate: peticiones por segundo
- **E**rrors: peticiones que fallan por segundo
- **D**uration: distribución de latencia (p50, p99)

En este proyecto: `payments_created_total` (rate), errores HTTP 422/5xx
(errors), `payments_amount_euros` y `http.server.request.duration` (duration).

**USE method** (para recursos, lo que consume el sistema):
- **U**tilization: porcentaje de uso (CPU, memoria, disco)
- **S**aturation: cuánto trabajo en cola que no se puede atender
- **E**rrors: errores del recurso

En este proyecto: lo cubren Node Exporter (host), cAdvisor (contenedores) y
postgres_exporter (base de datos).

Regla práctica: RED para tus servicios, USE para tu infraestructura.

### SLI, SLO, SLA

Tres siglas que se confunden constantemente:

- **SLI** (Indicator): la métrica concreta que mides. "Porcentaje de peticiones
  que responden en menos de 300ms".
- **SLO** (Objective): el objetivo interno sobre ese SLI. "99.5% de las
  peticiones bajo 300ms en 30 días".
- **SLA** (Agreement): el contrato con el cliente y sus penalizaciones si no
  se cumple. "Si bajamos del 99.5%, devolvemos el 10% de la factura".

El SLO es la herramienta de trabajo del SRE. De él sale el **error budget**:
si tu SLO es 99.5%, tienes un 0.5% de "presupuesto de error" que puedes gastar
en despliegues arriesgados o mantenimiento. Cuando se agota, se congelan los
cambios y se prioriza estabilidad.

### Cómo encaja todo en este proyecto

```
Instrumentación (OTel SDK en la app)
        |
   Recolección (push OTLP + pull scraping)
        |
   Procesamiento y routing (OTEL Collector)
        |
   Almacenamiento (Prometheus/Thanos, Loki, Tempo -> MinIO)
        |
   Visualización y correlación (Grafana)
        |
   Alertas (Prometheus rules -> Alertmanager)
```

Cada fase del proyecto añade una pieza de esta cadena. Tenerla entera en la
cabeza ayuda a ubicar cada herramienta en su sitio.

---

<a name="3-arquitectura-final"></a>
## 🏗️ 3. Arquitectura final

```
┌─────────────────────────────────────────────────────────────────┐
│                        FastAPI Payment API                      │
│              GET /health · GET /payments · POST /payments       │
└────────────────────────────┬────────────────────────────────────┘
                             │
                    ┌────────▼────────┐
                    │  OTEL Collector │  recepción · normalización
                    │                 │  enriquecimiento · routing
                    └──┬──────┬──────┬┘
                       │      │      │
           ┌───────────▼─┐  ┌─▼───┐ ┌▼─────┐
           │ Prometheus  │  │Loki │ │Tempo │
           │  métricas   │  │logs │ │trazas│
           └──────┬──────┘  └──┬──┘ └──┬───┘
                  │            │        │
           ┌──────▼────────────▼────────▼──────┐
           │             MinIO (S3)            │
           │  object storage S3-compatible     │
           │  bloques Thanos · chunks Loki     │
           │  datos Tempo (retención larga)    │
           └──────────────────────┬────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │         Thanos             │
                    │  Sidecar · Query · StoreGW │
                    └─────────────┬──────────────┘
                                  │
                    ┌─────────────▼──────────────┐
                    │           Grafana          │
                    │  métricas · logs · trazas  │
                    └────────────────────────────┘
```

---

<a name="4-requisitos-del-sistema"></a>
## 🖥️ 4. Requisitos del sistema

| Recurso | Mínimo | Probado con |
|---|---|---|
| SO | Ubuntu 22.04+ | Ubuntu 24.04 |
| RAM | 8 GB (Fases 1-4) | 16 GB (stack completo Fase 6) |
| CPU | 4 cores | 4 cores (i7) |
| Disco | 10 GB libres | 20 GB libres |
| Docker Engine | 24+ | última estable |
| Docker Compose | v2 (`docker compose`) | última estable |
| Python | 3.12+ | 3.14 |

```bash
# Verificar antes de empezar
docker --version
docker compose version
python3 --version
```

---

<a name="5-estructura-del-proyecto"></a>
## 📁 5. Estructura del proyecto

```
fastapi-observability/
│
├── app/
│   ├── app.py                    # FastAPI app (endpoints, modelos, métricas)
│   ├── Dockerfile
│   ├── gunicorn.conf.py
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py
│       └── test_api.py
│
├── prometheus/
│   ├── prometheus.yml            # Config scraping (Fase 2)
│   └── rules/
│       └── payment-api.yml       # Reglas de alerta (Fase 8)
│
├── grafana/
│   ├── provisioning/
│   │   ├── datasources/
│   │   │   ├── prometheus.yml    # Datasource Prometheus (Fase 2)
│   │   │   ├── loki.yml          # Datasource Loki (Fase 3)
│   │   │   └── tempo.yml         # Datasource Tempo + correlación (Fase 4)
│   │   └── dashboards/
│   │       └── dashboard.yml     # Config carga de dashboards (Fase 2)
│   └── dashboards/
│       ├── payment-api/
│       │   └── payments.json     # Dashboard de pagos (Fase 2)
│       └── infrastructure/
│           ├── node-exporter.json  # Dashboard Node Exporter (Fase 2)
│           ├── postgresql.json     # Dashboard PostgreSQL (Fase 2)
│           └── cadvisor.json       # Dashboard cAdvisor (Fase 2)
│
├── otel-collector/
│   └── otel-collector-config.yml # Pipeline unificado (Fase 5)
│
├── loki/
│   └── loki-config.yml           # Config Loki, backend S3/MinIO (Fase 3, 7)
│
├── tempo/
│   └── tempo-config.yml          # Config Tempo, backend S3/MinIO (Fase 4, 7)
│
├── thanos/
│   └── bucket-config.yml         # Config MinIO/S3 (Fase 6)
│
├── alertmanager/
│   └── alertmanager.yml          # Config enrutamiento de alertas (Fase 8)
│
├── fraud-service/
│   ├── main.go                   # Servicio Go con OTel (Fase 10)
│   ├── go.mod
│   └── Dockerfile
│
├── docker-compose.yml            # Crece fase a fase
├── .github/
│   ├── dependabot.yml
│   └── workflows/ci.yml
├── .gitignore
├── LICENSE
└── README.md
```

---

<a name="6-itinerario-de-fases"></a>
## 🗺️ 6. Itinerario de fases

---

### 🟢 Fase 1: La API

**Objetivo:** construir la API de pagos en FastAPI y dejarla funcionando
en local con Docker y PostgreSQL. Esta fase no toca nada de observabilidad,
es la base sobre la que construiremos todo lo demás.

**Qué se hace:**
- Crear `app.py` con FastAPI y Uvicorn
- Modelos Pydantic para request y response con validación automática
- Documentación OpenAPI gratuita en `/docs`
- PostgreSQL como base de datos con SQLAlchemy
- Tests con pytest y test gate en el pipeline de CI
- Arrancar con `docker compose up`

**Al terminar esta fase tendrás:**
```
FastAPI (/health · /payments)
    ↕
PostgreSQL
```

**URLs:**
| Servicio | URL |
|---|---|
| API docs (Swagger) | http://localhost:8000/docs |
| API docs (ReDoc) | http://localhost:8000/redoc |
| API health | http://localhost:8000/health |
| API pagos | http://localhost:8000/payments |

**Entorno virtual local (solo para el IDE):**

Los paquetes se instalan dentro de Docker, por lo que VS Code y Pylance no los
encuentran por defecto y muestra warnings de imports no resueltos. La solución
es crear un entorno virtual local únicamente para que el IDE tenga contexto:

```bash
cd app
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

Después en VS Code: `Ctrl+Shift+P` -> `Python: Select Interpreter` -> seleccionar
el intérprete que apunta a `app/.venv/bin/python`.

El directorio `.venv` ya está en `.gitignore` y no se sube al repositorio.

**Servidor de producción: Gunicorn + Uvicorn workers**

FastAPI necesita un servidor ASGI para funcionar. Hay varias opciones y la elección
importa en producción.

Alternativas consideradas:

| Opción | Descripción | Apto para producción |
|---|---|---|
| `uvicorn` (1 worker) | Simple, arranque rápido | No (sin redundancia) |
| `uvicorn --workers N` | Múltiples procesos, sin process manager | Parcialmente |
| `gunicorn + UvicornWorker` | Gunicorn gestiona workers Uvicorn | Si (opción elegida) |

La opción elegida es **Gunicorn con workers de tipo UvicornWorker**. Gunicorn actúa
como process manager: si un worker muere lo reinicia automáticamente, gestiona señales
del sistema operativo (SIGTERM, SIGHUP) y permite graceful shutdown, algo crítico en
Kubernetes cuando un pod se destruye con peticiones en curso.

La configuración vive en `gunicorn.conf.py` y cubre:

- **workers**: calculado como `(CPU * 2) + 1`, sobreescribible con `WEB_CONCURRENCY`
- **worker_class**: `uvicorn.workers.UvicornWorker` para soporte async completo
- **max_requests / jitter**: reinicio periódico de workers para evitar memory leaks
- **graceful_timeout**: tiempo para terminar peticiones en curso antes de matar el proceso
- **timeout**: mata workers bloqueados sin respuesta
- **accesslog / errorlog**: redirigidos a stdout/stderr para que Docker los recoja

En local se fija `WEB_CONCURRENCY=2` en el `docker-compose.yml`. En producción
se sube ese valor via variable de entorno sin tocar el código ni la imagen.

Para profundizar:

| Recurso | Enlace |
|---|---|
| Uvicorn deployment | https://uvicorn.dev/deployment/ |
| Gunicorn settings | https://gunicorn.org/reference/settings/ |
| FastAPI server workers | https://fastapi.tiangolo.com/deployment/server-workers/ |

YouTube:
- **ArjanCodes** (FastAPI producción) -> https://www.youtube.com/@ArjanCodes/search?query=fastapi+production
- **ArjanCodes** (SQLAlchemy) -> https://www.youtube.com/@ArjanCodes/search?query=sqlalchemy
- **Corey Schafer** (SQLAlchemy, fundamentos) -> https://www.youtube.com/@coreyms/search?query=sqlalchemy
- **TechWorld with Nana** (FastAPI + Docker) -> https://www.youtube.com/@TechWorldwithNana/search?query=fastapi+docker
- **That DevOps Guy** (Python en producción) -> https://www.youtube.com/@MarcelDempers/search?query=python

**Para profundizar antes de continuar:**

| Recurso | Enlace | Para qué sirve |
|---|---|---|
| FastAPI Tutorial oficial | https://fastapi.tiangolo.com/tutorial/ | Conceptos base: path params, query params, body, response models |
| Pydantic docs | https://docs.pydantic.dev | Validación y modelos de datos |
| Testing en FastAPI | https://fastapi.tiangolo.com/tutorial/testing/ | Tests con `pytest` y `httpx` |
| SQLAlchemy 2.0 | https://docs.sqlalchemy.org/en/20/ | ORM y capa de base de datos |
| GitHub Actions docs | https://docs.github.com/en/actions | Workflows, jobs, steps, servicios |

YouTube recomendado:
- Canal **ArjanCodes** -> https://www.youtube.com/@ArjanCodes/search?query=fastAPI (buenas prácticas)
- Canal **TechWorld with Nana** -> https://www.youtube.com/@TechWorldwithNana/search?query=GitHub%20Actions (CI/CD desde cero)

**Tests con pytest:**

Los tests viven en `app/tests/` y usan `TestClient` de FastAPI junto con una
base de datos PostgreSQL de test independiente de la de desarrollo.

Las dependencias de test están separadas en `requirements-dev.txt` para que
no entren en la imagen Docker de producción:

```
pytest==9.0.3
httpx==0.28.1
```

Ejecutar tests en local:

```bash
cd app
DATABASE_URL=postgresql://payments:payments@localhost:5432/paymentsdb_test \
  pytest tests/ -v
```

Casos cubiertos:

| Test | Qué verifica |
|---|---|
| `test_health` | Respuesta correcta del healthcheck |
| `test_get_payments` | Estructura del listado: `payments` y `total` |
| `test_create_payment_valid` | Flujo completo: 201, campos presentes y valores correctos |
| `test_create_payment_negative_amount` | Importe negativo -> 422 |
| `test_create_payment_zero_amount` | Importe cero -> 422 (límite estricto `gt=0`) |
| `test_create_payment_invalid_currency` | Moneda fuera de patrón `^[A-Z]{3}$` -> 422 |
| `test_create_payment_missing_amount` | Campo obligatorio ausente -> 422 |
| `test_get_payments_after_creation` | El total del listado incrementa tras crear un pago |

**Pipeline CI con test gate:**

El workflow de GitHub Actions en `.github/workflows/ci.yml` tiene dos jobs:

```
test -> build-and-push
```

El job `test` levanta PostgreSQL como servicio, instala dependencias y ejecuta
pytest. El job `build-and-push` tiene `needs: test`, de forma que si los tests
fallan la imagen nunca se construye ni se publica en GHCR.

```yaml
jobs:
  test:
    services:
      postgres:           # PostgreSQL efímero solo para los tests
        image: postgres:18-alpine
    steps:
      - run: pytest tests/ -v

  build-and-push:
    needs: test           # bloquea el build si test falla
    steps:
      - build y push a GHCR
```

**Pruebas de validación:**

```bash
# Health check
curl http://localhost:8000/health

# Crear pago válido
curl -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "EUR"}'

# Listar pagos
curl http://localhost:8000/payments

# Importe negativo -> 422 Unprocessable Content
curl -i -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": -50, "currency": "EUR"}'

# Moneda inválida -> 422 Unprocessable Content
curl -i -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "currency": "eurosss"}'

# Sin importe -> 422 Unprocessable Content
curl -i -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"currency": "EUR"}'
```


**Conceptos que afianzas aquí:**

Antes de poder observar algo, ese algo tiene que existir y comportarse de forma
realista. Esta fase fija la idea de que la observabilidad se construye sobre un
servicio con estado (base de datos, endpoints, errores posibles), no sobre un
"hola mundo". El patrón de health endpoint (`/health`) que creas aquí es el
mismo que luego usan Kubernetes (liveness/readiness probes) y el blackbox
exporter (Fase 8) para decidir si el servicio está vivo. Aprendes que la
instrumentación empieza por diseñar la app para que sea observable.

---

### 🔵 Fase 2: Métricas con Prometheus y Grafana

**Concepto:** las métricas son la señal más básica de observabilidad.
Responden a preguntas como: cuántas peticiones por segundo recibo,
cuánto tardan, cuántos pagos se crean por minuto, hay errores.

Prometheus recoge estas métricas cada 15 segundos haciendo scraping
al endpoint `/metrics` que expone la app. Grafana las visualiza.

**Vídeo previo recomendado:**
YouTube -> canal **TechWorld with Nana** -> https://www.youtube.com/@TechWorldwithNana/search?query=Prometheus+Grafana

**Tipos de métricas:**

| Tipo | Descripción | Ejemplo en pagos |
|---|---|---|
| Counter | Solo sube, nunca baja. Se resetea al reiniciar | Pagos creados, requests totales |
| Gauge | Sube y baja libremente, refleja estado actual | Workers activos, RAM usada |
| Histogram | Distribución de valores, permite calcular percentiles | Tiempo de respuesta, importes |

**Qué se hace:**
- Añadir `prometheus-fastapi-instrumentator` a `app.py`, que expone `/metrics` automáticamente con métricas HTTP
- Añadir métricas custom de negocio con `prometheus-client`:
  - `payments_created_total` (Counter) con etiqueta `currency`
  - `payments_amount_euros` (Histogram) con buckets por importe y etiqueta `currency`
- Crear `prometheus/prometheus.yml` con la configuración de scraping cada 15s
- Crear provisioning de Grafana: datasource Prometheus y dashboards pre-configurados
- Añadir Prometheus y Grafana al `docker-compose.yml`
- Añadir exporters de infraestructura para monitorizar el servidor y los contenedores
- Verificar todos los targets en Prometheus y explorar dashboards en Grafana

**Exporters de infraestructura:**

En producción no basta con monitorizar la aplicación. Los exporters son agentes
que exponen métricas de sistemas que no hablan Prometheus de forma nativa.

| Exporter | Imagen | Puerto | Qué monitoriza |
|---|---|---|---|
| Node Exporter | `prom/node-exporter` | 9100 | CPU, RAM, disco, red del servidor Linux |
| postgres_exporter | `prometheuscommunity/postgres-exporter` | 9187 | Conexiones, queries, estado de PostgreSQL |
| cAdvisor | `gcr.io/cadvisor/cadvisor` | 8080 | RAM y CPU por contenedor Docker |

Los tres se añaden como servicios en `docker-compose.yml` y como jobs en `prometheus.yml`.

**Dashboards de Grafana provisionados:**

Los dashboards de la comunidad se descargan y se incluyen en el repo como código.
Al arrancar Grafana los carga automáticamente sin intervención manual.

```bash
mkdir -p grafana/dashboards/payment-api
mkdir -p grafana/dashboards/infrastructure

# Dashboard de la app (creado a mano)
# grafana/dashboards/payment-api/payments.json

# Dashboards de la comunidad
curl -s https://grafana.com/api/dashboards/1860/revisions/latest/download \
  -o grafana/dashboards/infrastructure/node-exporter.json
curl -s https://grafana.com/api/dashboards/9628/revisions/latest/download \
  -o grafana/dashboards/infrastructure/postgresql.json
curl -s https://grafana.com/api/dashboards/193/revisions/latest/download \
  -o grafana/dashboards/infrastructure/cadvisor.json

# Los dashboards de comunidad usan una variable de datasource que Grafana
# no resuelve al cargar desde fichero. Hay que reemplazarla con el nombre real:
sed -i 's/\${DS_PROMETHEUS}/Prometheus/g' grafana/dashboards/infrastructure/node-exporter.json
sed -i 's/\${DS_PROMETHEUS}/Prometheus/g' grafana/dashboards/infrastructure/postgresql.json
sed -i 's/\${DS_PROMETHEUS}/Prometheus/g' grafana/dashboards/infrastructure/cadvisor.json
```

**Ficheros nuevos:**
```
prometheus/
└── prometheus.yml
grafana/
├── provisioning/
│   ├── datasources/
│   │   └── prometheus.yml
│   └── dashboards/
│       └── dashboard.yml
└── dashboards/
    ├── payment-api/
    │   └── payments.json
    └── infrastructure/
        ├── node-exporter.json
        ├── postgresql.json
        └── cadvisor.json
```

**Bugs encontrados y corregidos durante la implementación:**
- `payments_amount_euros.observe()` recibía un `Decimal` de SQLAlchemy en vez de `float` -> fix: `float(new_payment.amount)`
- El CI marcaba verde aunque pytest fallaba porque `| tee` ocultaba el exit code de pytest -> fix: `set -o pipefail`
- El CI no arrancaba en PRs de Dependabot porque el trigger solo cubría `push` a `main` -> fix: añadir trigger `pull_request`
- Los dashboards de comunidad mostraban "No data" al provisionar desde fichero -> fix: `sed` para reemplazar `${DS_PROMETHEUS}`

**Generar tráfico para ver los paneles:**
```bash
for i in {1..20}; do
  curl -s -X POST http://localhost:8000/payments \
    -H "Content-Type: application/json" \
    -d "{\"amount\": $((RANDOM % 1000 + 1)).99, \"currency\": \"EUR\"}" > /dev/null
  sleep 0.5
done
```

**Al terminar esta fase tendrás:**
```
FastAPI (/metrics) <-- scraping cada 15s -- Prometheus
Node Exporter      <-- scraping cada 15s -- Prometheus
postgres_exporter  <-- scraping cada 15s -- Prometheus
cAdvisor           <-- scraping cada 15s -- Prometheus
                                                  ↕
                                              Grafana
                               (Payment API · Infrastructure)
```

**URLs añadidas:**
| Servicio | URL |
|---|---|
| Métricas raw | http://localhost:8000/metrics |
| Node Exporter | http://localhost:9100/metrics |
| postgres_exporter | http://localhost:9187/metrics |
| cAdvisor | http://localhost:8080/metrics |
| Prometheus targets | http://localhost:9090/targets |
| Prometheus query | http://localhost:9090 |
| Grafana | http://localhost:3000 (admin/admin) |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Prometheus docs | https://prometheus.io/docs/introduction/overview/ |
| PromQL basics | https://prometheus.io/docs/prometheus/latest/querying/basics/ |
| Prometheus exporters | https://prometheus.io/docs/instrumenting/exporters/ |
| Node Exporter | https://github.com/prometheus/node_exporter |
| postgres_exporter | https://github.com/prometheus-community/postgres_exporter |
| cAdvisor | https://github.com/google/cadvisor |
| Grafana dashboards | https://grafana.com/docs/grafana/latest/dashboards/ |
| Grafana provisioning | https://grafana.com/docs/grafana/latest/administration/provisioning/ |
| prometheus-fastapi-instrumentator | https://github.com/trallnag/prometheus-fastapi-instrumentator |

YouTube:
- Canal **TechWorld with Nana** (Prometheus) -> https://www.youtube.com/@TechWorldwithNana/search?query=Prometheus+Grafana
- Canal **TechWorld with Nana** (Node Exporter) -> https://www.youtube.com/@TechWorldwithNana/search?query=node+exporter
- Canal **That DevOps Guy** (Prometheus exporters) -> https://www.youtube.com/@MarcelDempers/search?query=prometheus+exporter


**Conceptos que afianzas aquí:**

Aquí interiorizas el modelo pull de Prometheus: el servidor va a buscar las
métricas a un endpoint, no al revés. Entiendes la diferencia entre los tipos de
métrica (Counter que solo sube, Gauge que oscila, Histogram que distribuye en
buckets) y por qué `rate()` solo tiene sentido sobre counters. Empiezas a notar
el peso de la cardinalidad: cada label que añades multiplica las series. Es el
primer pilar (métricas) y el método RED aplicado a un servicio real de pagos.

---

### 🟡 Fase 3: Logs estructurados con Loki

**Concepto:** los logs son la señal más detallada. Un log bien estructurado
te dice exactamente qué pasó, cuándo, en qué contexto y con qué datos.

El problema habitual es que los logs son texto plano y son imposibles de
buscar a escala:

```
# Log plano (inútil para buscar por campo)
2026-05-25 19:30:01 INFO Payment created amount=99.99 currency=EUR

# Log estructurado JSON (filtrable por cualquier campo)
{"event": "payment_created", "level": "info", "timestamp": "2026-05-25T19:30:01Z",
 "payment_id": "dc56ff88", "amount": 99.99, "currency": "EUR"}
```

Promtail recoge los logs de todos los contenedores Docker automáticamente
y los envía a Loki. Grafana los visualiza y permite consultarlos con LogQL.

**Vídeo previo recomendado:**
YouTube -> canal **Grafana** oficial -> https://www.youtube.com/@Grafana/search?query=loki+docker

**Qué se hace:**
- Añadir `structlog` a `app.py` para emitir logs JSON estructurados
- Crear `loki/loki-config.yml` con la configuración de Loki
- Crear `promtail/promtail-config.yml` para recoger logs de todos los contenedores Docker via socket
- Añadir datasource Loki en Grafana via provisioning
- Explorar LogQL en Grafana: filtrar por campo, nivel, servicio

**Scope de logs recogidos:**
- Logs JSON estructurados de FastAPI (evento `payment_created` con `payment_id`, `amount`, `currency`)
- Logs de todos los contenedores Docker (PostgreSQL, Prometheus, Grafana, cAdvisor...) via Promtail

**Ficheros nuevos:**
```
loki/
└── loki-config.yml
promtail/
└── promtail-config.yml
grafana/provisioning/datasources/
└── loki.yml
```

**Queries LogQL de ejemplo:**

```logql
# Todos los logs de la API
{service="api"}

# Solo logs del evento payment_created
{service="api"} |= "payment_created"

# Filtrar por campo JSON: pagos con importe mayor de 500€
{service="api"} | json | amount > 500

# Solo logs de nivel error
{service="api"} | json | level="error"

# Logs de PostgreSQL
{service="db"}

# Todos los contenedores menos Prometheus
{container=~".+"} != "prometheus"
```

**Bug encontrado durante la implementación:**
- Loki 3.x corre como usuario no-root (uid 10001) y no tiene permisos para escribir
  en volúmenes Docker creados como root -> fix: eliminar el volumen externo y dejar
  que Loki use `/tmp/loki` internamente dentro del contenedor

**Nota sobre Promtail:**
Promtail está en modo mantenimiento. Grafana lo está reemplazando por **Grafana Alloy**,
su nuevo agente unificado. Para este proyecto Promtail es válido hasta la Fase 4.
En la Fase 5 es eliminado del stack y reemplazado por el OTEL Collector,
que recibe logs directamente de la app via OTLP.

**Al terminar esta fase tendrás:**
```
FastAPI (JSON logs)         ---> Promtail ---> Loki
Todos los contenedores      ---> Promtail ---> Loki
                                                ↕
                                            Grafana (Explore -> Loki)
```

**URLs añadidas:**
| Servicio | URL |
|---|---|
| Loki (health) | http://localhost:3100/ready |
| Grafana (Loki) | http://localhost:3000 -> Explore -> Loki |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Grafana Loki docs | https://grafana.com/docs/loki/latest/ |
| LogQL reference | https://grafana.com/docs/loki/latest/query/ |
| Promtail docs | https://grafana.com/docs/loki/latest/send-data/promtail/ |
| structlog docs | https://www.structlog.org/en/stable/ |
| Grafana Alloy (sucesor de Promtail) | https://grafana.com/docs/alloy/latest/ |

YouTube:
- Canal **Grafana** (Loki) -> https://www.youtube.com/@Grafana/search?query=loki
- Canal **TechWorld with Nana** (Loki) -> https://www.youtube.com/@TechWorldwithNana/search?query=loki
- Canal **That DevOps Guy** (Loki) -> https://www.youtube.com/@MarcelDempers/search?query=loki


**Conceptos que afianzas aquí:**

El salto mental clave es entender por qué un log estructurado (JSON con campos)
es infinitamente más útil que una línea de texto plano: puedes filtrar y agregar
por campo. Comprendes el modelo de Loki, que no indexa el contenido del log sino
solo unos pocos labels (de baja cardinalidad, igual que en métricas), lo que lo
hace barato. Este es el segundo pilar (logs) y aquí plantas la semilla de la
correlación al empezar a incluir el `trace_id` en cada log.

---

### 🟣 Fase 4: Trazas distribuidas con Tempo y OpenTelemetry

**Concepto:** una traza muestra el recorrido completo de una petición desde que entra
hasta que sale. Está formada por spans, uno por cada operación del camino:

```
Traza: POST /payments (29.73ms total)
  ├── span: POST /payments http receive    (66µs)
  ├── span: payment.process               (26ms)   <- span custom
  │     ├── span: connect                 (722µs)  <- SQLAlchemy auto
  │     ├── span: INSERT paymentsdb       (2.43ms) <- SQLAlchemy auto
  │     ├── span: connect                 (1.51ms) <- SQLAlchemy auto
  │     └── span: SELECT paymentsdb       (3.43ms) <- SQLAlchemy auto
  └── span: POST /payments http send      (45µs)
```

Las métricas te dicen qué está lento. Las trazas te dicen dónde exactamente.

**¿Qué es Tempo?**

Tempo es el backend de almacenamiento de trazas de Grafana Labs. A diferencia de
Jaeger o Zipkin, Tempo no indexa por defecto — guarda las trazas en object storage
sin índices pesados, lo que lo hace muy barato de operar a escala. Se integra
nativamente con Grafana y acepta el protocolo OTLP estándar.

```
FastAPI --OTLP HTTP (4318)--> Tempo (almacena)
Grafana --HTTP (3200)-------> Tempo (consulta)
```

**¿Por qué el OTEL Collector va en la Fase 5 y no ahora?**

En esta fase el flujo es directo: `FastAPI -> Tempo`. En la Fase 5 el Collector
se pone en medio: `FastAPI -> OTEL Collector -> Tempo/Prometheus/Loki`.

El Collector aporta valor cuando tienes múltiples señales y múltiples destinos:
la app solo habla con un sitio, el Collector decide adónde va cada señal, y si
cambias de backend (de Tempo a Jaeger por ejemplo) solo cambias la config del
Collector sin tocar la app.

**¿Las trazas son solo de la app?**

Las trazas son para aplicaciones, no para infraestructura. Node Exporter o
cAdvisor emiten métricas, no trazas. PostgreSQL no se traza directamente, pero
con `opentelemetry-instrumentation-sqlalchemy` cada query SQL se convierte en
un span dentro de la traza de la petición, mostrando exactamente cuánto tiempo
tarda cada operación de BD.

En arquitecturas con varios microservicios el valor se multiplica: una sola
traza con el mismo `trace_id` recorre todos los servicios.

**Vídeo previo recomendado:**
YouTube -> canal **opentelemetry** oficial -> https://www.youtube.com/@otel-official/search?query=python+fastapi

**Qué se hace:**
- Añadir dependencias OpenTelemetry al `requirements.txt`
- Configurar `TracerProvider` con `OTLPSpanExporter` apuntando a Tempo (HTTP puerto 4318)
- Inyectar `trace_id` y `span_id` en cada log de structlog via `add_trace_context`
- Auto-instrumentación de FastAPI con `FastAPIInstrumentor`
- Auto-instrumentación de SQLAlchemy con `SQLAlchemyInstrumentor`
- Span custom `payment.process` con atributos de negocio (`payment.amount`, `payment.currency`)
- Definir el service name directamente en el código via `Resource.create({SERVICE_NAME: "payment-api"})`
- Crear `tempo/tempo-config.yml` con receptor OTLP HTTP y gRPC
- Añadir datasource Tempo en Grafana con correlación configurada hacia Loki
- Verificar correlación traza -> logs en Grafana

**Dependencias añadidas:**
```
opentelemetry-api==1.41.1
opentelemetry-sdk==1.41.1
opentelemetry-instrumentation-fastapi==0.62b1
opentelemetry-instrumentation-sqlalchemy==0.62b1
opentelemetry-exporter-otlp-proto-http==1.41.1
```

**Ficheros nuevos:**
```
tempo/
└── tempo-config.yml
grafana/provisioning/datasources/
└── tempo.yml
```

**Correlación traza -> logs:**

El campo `trace_id` es el hilo conductor. Cada log JSON de structlog incluye
el `trace_id` del span activo en ese momento. Al abrir una traza en Grafana
y pulsar el icono de logs junto a un span, Grafana ejecuta automáticamente
esta query en Loki:

```logql
{container="payment-api"} | trace_id="0427e8800e08decfeb5dde07610ea6fc"
```

Y muestra el log exacto de ese pago con todos sus campos.

**Bugs encontrados durante la implementación:**
- Usar `OTEL_SERVICE_NAME` como variable de entorno conflictuaba con la
  auto-configuración del SDK -> fix: definir el service name directamente en
  el código con `Resource.create({SERVICE_NAME: "payment-api"})` y eliminar
  la env var
- La correlación Tempo -> Loki usaba el label `service_name` que no existe en
  Loki -> fix: mapear `service.name` al label `container` en el datasource de
  Tempo, que sí existe en Promtail

**Upgrade a Tempo 3.0 (PR Dependabot #13):**

Tempo 3.0 es un major release con breaking changes. El CI de Dependabot pasa
en verde porque solo valida que la imagen arranca, no que el config es válido.
Hay que probarlo manualmente antes de mergear.

Breaking change principal: el campo `compactor` fue eliminado del config.
En Tempo 3.0 la compactación y retención las gestiona el nuevo `backend_scheduler`
con sus componentes `block-builders`, `live-stores` y `backend-worker`.

Config en Tempo 2.x que dejó de funcionar:
```yaml
compactor:
  compaction:
    block_retention: 48h
```

Error al arrancar con Tempo 3.0:
```
failed parsing config: field compactor not found in type app.Config
```

Fix: eliminar el bloque `compactor` del config. La retención por defecto en
Tempo 3.0 es 14 días, suficiente para este proyecto. Si necesitas configurarla
explícitamente, se hace bajo `backend_scheduler.provider`.

Config mínimo funcional con Tempo 3.0:
```yaml
server:
  http_listen_port: 3200

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318

storage:
  trace:
    backend: local
    local:
      path: /tmp/tempo/blocks
    wal:
      path: /tmp/tempo/wal
```

Tempo 3.0 también introduce en modo monolítico (nuestro caso):
- `live_store` para servir trazas recientes desde memoria
- `backend_scheduler` para compactación y retención job-based
- `retention provider` como componente independiente
- Nuevas arquitecturas desacopladas para modo microservicios (Kafka-based)

En modo monolítico todo corre en un único proceso sin Kafka, igual que en 2.x.

**Al terminar esta fase tendrás:**
```
FastAPI --OTLP HTTP--> Tempo
                          ↕
                       Grafana (ver traza + saltar a logs de Loki)
```

**URLs añadidas:**
| Servicio | URL |
|---|---|
| Tempo (health) | http://localhost:3200/ready |
| Grafana (Tempo) | http://localhost:3000 -> Explore -> Tempo |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Grafana Tempo docs | https://grafana.com/docs/tempo/latest/ |
| OpenTelemetry Python | https://opentelemetry.io/docs/languages/python/ |
| OTel SDK Python | https://opentelemetry-python.readthedocs.io/ |
| OTel FastAPI instrumentation | https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/fastapi/fastapi.html |
| OTel SQLAlchemy instrumentation | https://opentelemetry-python-contrib.readthedocs.io/en/latest/instrumentation/sqlalchemy/sqlalchemy.html |
| TraceQL reference | https://grafana.com/docs/tempo/latest/traceql/ |

YouTube:
- Canal **opentelemetry** oficial -> https://www.youtube.com/@otel-official/search?query=python
- Canal **Grafana** (Tempo) -> https://www.youtube.com/@Grafana/search?query=tempo
- Canal **That DevOps Guy** (OpenTelemetry) -> https://www.youtube.com/@MarcelDempers/search?query=opentelemetry


**Conceptos que afianzas aquí:**

Esta fase fija el concepto de traza y span: una petición es un árbol de
operaciones con tiempos, no un evento plano. Entiendes el rol del SDK de
OpenTelemetry como capa de instrumentación vendor-neutral, y la diferencia entre
auto-instrumentación (FastAPI, SQLAlchemy) e instrumentación manual (tus propios
spans). Es el tercer pilar (trazas) y el momento en que el `trace_id` se
convierte en el hilo que cose métricas, logs y trazas.

---

### 🟠 Fase 5: OTEL Collector, el router central

**Concepto:** en producción no se envían trazas, logs y métricas directamente
a sus backends. Todo pasa por un Collector centralizado que recibe, normaliza,
enriquece y enruta cada señal. La app tiene un único punto de salida.

**¿Qué es el OTEL Collector?**

Es un proceso independiente que actúa de intermediario entre la app y los
backends de observabilidad. Su arquitectura se basa en tres etapas por señal:

```
receivers -> processors -> exporters
```

Pipeline completo de esta fase:
```yaml
receivers:
  otlp:          # recibe trazas, logs y métricas de la app por gRPC
  prometheus:    # scraping de Node Exporter, postgres_exporter, cAdvisor

processors:
  memory_limiter:  # evita consumo ilimitado de RAM
  batch:           # agrupa señales antes de enviar (eficiencia)
  resource:        # añade atributos a todas las señales (environment, etc.)

exporters:
  otlp_grpc/tempo:         # trazas a Tempo
  otlp_http/loki:          # logs a Loki
  prometheusremotewrite:   # métricas a Prometheus via remote write
```

**¿Por qué merece la pena?**

Si mañana cambias Tempo por Jaeger, solo tocas el Collector. La app no cambia.
Puedes enriquecer señales, filtrar ruido (health checks), y tener un único
punto de salida en vez de múltiples conexiones a múltiples backends.

**Qué se hace:**
- Eliminar Promtail del stack
- Eliminar `prometheus-fastapi-instrumentator` y `prometheus-client` de la app
- Añadir OTel Collector con pipelines de trazas, logs y métricas
- Configurar la app para enviar las tres señales via OTLP gRPC
- Bridgear structlog con Python stdlib logging y OTel LoggingHandler
- Migrar métricas de `prometheus_client` a OTel metrics SDK
- Activar `--web.enable-remote-write-receiver` en Prometheus
- Collector scraping los exporters de infraestructura (Node Exporter, postgres_exporter, cAdvisor)
- Prometheus deja de scrapear la app directamente

**Flujo final:**
```
FastAPI --OTLP gRPC (trazas + logs + métricas)--> OTEL Collector
                                                        |
                       +----------------+---------------+-----------+
                       |                |                           |
               otlp_grpc/tempo   otlp_http/loki     prometheusremotewrite
                       |                |                           |
                    Tempo             Loki                    Prometheus
                                                                    |
                                                 (también recibe métricas de
                                                  Node Exporter, postgres_exporter
                                                  y cAdvisor via prometheus receiver)
```

**Ficheros modificados:**
```
otel-collector/
└── otel-collector-config.yml   # pipelines trazas + logs + métricas
app/
├── app.py                      # OTel trazas + logs + métricas, elimina prometheus_client
└── requirements.txt            # elimina prometheus-fastapi-instrumentator y prometheus-client
app/tests/
├── test_api.py                 # elimina test_metrics_endpoint (/metrics ya no existe)
└── requirements-dev.txt        # httpx -> httpx2
prometheus/
└── prometheus.yml              # solo self-monitoring + otel-collector, Collector asume el resto
docker-compose.yml              # añade otel-collector, elimina promtail,
                                # activa --web.enable-remote-write-receiver en Prometheus
.github/workflows/ci.yml        # añade OTEL_SDK_DISABLED=true en job de tests
```

**Cambios en `app.py`:**

La función `setup_tracing()` ahora configura las tres señales con el mismo
`resource` y el mismo endpoint OTLP. Todas apuntan a `otel-collector:4317`:

```python
def setup_tracing():
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return trace.get_tracer(__name__), MeterProvider().get_meter(__name__)

    resource = Resource.create({SERVICE_NAME: "payment-api"})

    # Trazas
    trace_exporter = OTLPSpanExporter(endpoint=..., insecure=True)
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(provider)

    # Logs
    log_exporter = OTLPLogExporter(endpoint=..., insecure=True)
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(log_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
    logging.getLogger().addHandler(handler)

    # Metricas
    metric_exporter = OTLPMetricExporter(endpoint=..., insecure=True)
    reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=15000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    set_meter_provider(meter_provider)
    meter = meter_provider.get_meter(__name__)

    return trace.get_tracer(__name__), meter

tracer, meter = setup_tracing()
```

Las métricas de negocio migran de `prometheus_client` a OTel:

```python
# Antes (prometheus_client)
payments_created_total = Counter("payments_created_total", ..., ["currency"])
payments_created_total.labels(currency=payment.currency).inc()

# Ahora (OTel)
payments_created_total = meter.create_counter("payments_created_total", ...)
payments_created_total.add(1, {"currency": payment.currency})
```

**Loki recibe logs OTLP con labels distintos a Promtail:**

Con Promtail los logs llegaban con `container="payment-api"`.
Con OTLP los logs llegan con `service_name="payment-api"`. Las queries cambian:

```logql
{service_name="payment-api"} |= "payment_created"
{service_name="payment-api"} | json | amount > 500
```

**Labels en Prometheus con OTel metrics:**

Las métricas OTel llegan a Prometheus con labels adicionales:
`job="payment-api"` (de `service.name`) y `otel_scope_name="app"` (del módulo).

**`prometheus.yml` tras la migración:**

Prometheus scraping directamente los exporters de infraestructura (no via Collector).
El Collector solo gestiona métricas OTLP de la app:

```yaml
scrape_configs:
  - job_name: prometheus
    static_configs:
      - targets: [localhost:9090]

  - job_name: otel-collector
    static_configs:
      - targets: [otel-collector:8888]

  - job_name: node-exporter
    static_configs:
      - targets: [node-exporter:9100]

  - job_name: postgres-exporter
    static_configs:
      - targets: [postgres-exporter:9187]

  - job_name: cadvisor
    static_configs:
      - targets: [cadvisor:8080]
```

**¿Por qué los exporters de infraestructura no pasan por el Collector?**

El pipeline `prometheus receiver -> prometheusremotewrite` rompe `rate()` en Grafana.
Los counters (node_cpu_seconds_total, node_network_bytes_total) pierden su semántica
durante la conversión interna OTEL. Además, si Prometheus scraping directamente Y el
Collector envía via remote write simultáneamente, aparecen datos duplicados con labels
extra (`otel_scope_name`) que ensucian los dashboards durante 7 días (retención).

La solución definitiva: exporters de infraestructura scrapeados directamente por
Prometheus. Métricas de la app via OTLP al Collector. Remote write solo para la app.

**Bugs encontrados durante la implementación:**

- El exporter `loki` fue eliminado del Collector contrib -> fix: usar `otlp_http/loki`
  con endpoint `http://loki:3100/otlp` (Loki 3.x acepta OTLP nativamente)
- Los aliases `otlp`, `otlphttp`, `filelog` deprecados -> fix: nombres sin alias
- `opentelemetry.sdk.logs` no existe en SDK 1.42.1 -> fix: `opentelemetry.sdk._logs`
- `file_log` con `start_at: beginning` causaba HTTP 429 en Loki por ráfaga masiva
  -> se descartó el enfoque file_log, la app envía logs directamente via OTLP
- La correlación Tempo -> Loki seguía usando label `container` porque Grafana 13
  auto-detecta labels disponibles -> fix: eliminar el datasource via API y reprovisionar
- El CI fallaba con `grpc._channel._InactiveRpcError` porque la app intentaba
  conectar a `otel-collector:4317` durante los tests -> fix: `OTEL_SDK_DISABLED=true`
  en el job de tests del CI y check en `setup_tracing()` para devolver no-op
- `test_metrics_endpoint` fallaba porque `/metrics` ya no existe -> fix: eliminar el test
- `httpx` deprecado en `starlette.testclient` -> fix: `httpx2==2.2.0`
- En OTEL Collector 0.153.0 el campo `address` bajo `service.telemetry.metrics`
  fue eliminado (`MetricsConfigV030` has invalid keys: address) -> fix: usar
  el nuevo formato `readers` con exporter Prometheus explícito:
  ```yaml
  service:
    telemetry:
      metrics:
        readers:
          - pull:
              exporter:
                prometheus:
                  host: 0.0.0.0
                  port: 8888
  ```
- El pipeline `prometheus receiver -> prometheusremotewrite` rompe `rate()` en dashboards
  de Grafana: los counters (node_cpu_seconds_total, node_network_*) pierden semántica
  en la conversión interna OTEL y aparecen como datos duplicados con labels extra
  (`otel_scope_name`) -> fix: Prometheus vuelve a scrapear directamente Node Exporter,
  postgres_exporter y cAdvisor; el Collector solo gestiona métricas OTLP de la app

**Por qué se descartó `file_log` para logs:**

El Collector puede leer ficheros Docker directamente pero tiene tres problemas:
rate limit de Loki por la ráfaga inicial, sin label de container name automático,
y complejidad de parseo. La solución correcta es que la app envíe logs via OTLP.

**Al terminar esta fase tendrás:**
```
FastAPI --OTLP (trazas + logs + métricas app)--> OTEL Collector --> Tempo / Loki / Prometheus
Prometheus --scraping directo--> Node Exporter / postgres_exporter / cAdvisor
                                          ↕
                                       Grafana
```

**URLs sin cambios:** las mismas de las fases anteriores.

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| OTEL Collector docs | https://opentelemetry.io/docs/collector/ |
| OTEL Collector config | https://opentelemetry.io/docs/collector/configuration/ |
| Prometheus receiver | https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver/prometheusreceiver |
| prometheusremotewrite exporter | https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/exporter/prometheusremotewriteexporter |
| OTel Python metrics | https://opentelemetry.io/docs/languages/python/instrumentation/#metrics |
| OTel Python logs | https://opentelemetry.io/docs/languages/python/instrumentation/#logs |
| Loki OTLP ingestion | https://grafana.com/docs/loki/latest/send-data/otel/ |
| Prometheus remote write | https://prometheus.io/docs/prometheus/latest/configuration/configuration/#remote_write |

YouTube:
- Canal **opentelemetry** oficial (Collector) -> https://www.youtube.com/@otel-official/search?query=collector
- Canal **Anton Putra** (OTEL Collector) -> https://www.youtube.com/@AntonPutra/search?query=opentelemetry+collector


**Conceptos que afianzas aquí:**

El concepto transferible es el del Collector como punto único de
recepción/procesamiento/routing: instrumentas la app una vez y decides el
destino en la config, sin tocar código. Aquí vives en carne propia por qué push
(OTLP) y pull (scraping) conviven y no se mezclan a la ligera: descubres que
meter counters por un pipeline equivocado rompe `rate()`. Entiendes que el
Collector desacopla la app de los backends concretos, que es la promesa central
de OpenTelemetry.

---

### 🔴 Fase 6: Retención larga con Thanos y MinIO

**Concepto:** Prometheus guarda métricas en disco local con retención limitada
(7 días en este proyecto). Si el contenedor muere, pierdes todo. Si quieres
consultar métricas de hace 3 meses, no puedes. Thanos resuelve ambos problemas.

**¿Qué es Thanos?**

Thanos es una extensión de Prometheus que añade retención larga, alta disponibilidad
y queries federadas a escala. Se compone de varios procesos independientes que
trabajan juntos. En esta fase usamos tres:

**Thanos Sidecar:** corre junto a Prometheus en el mismo pod/contenedor-group.
Hace dos cosas: expone los datos de Prometheus via gRPC (StoreAPI) para que
Thanos Query pueda consultarlos en tiempo real, y sube los bloques TSDB de
Prometheus a MinIO cada 2 horas.

**Thanos Store Gateway:** expone el contenido de MinIO (bloques históricos)
via gRPC (StoreAPI). Permite consultar datos que ya no están en Prometheus
local porque han superado la retención.

**Thanos Query:** el punto de entrada único para Grafana. Recibe una query
PromQL, la distribuye en paralelo a todos los endpoints (Sidecar + Store),
recoge los resultados, los deduplica y devuelve una respuesta unificada:

```
Grafana --> Thanos Query --> Thanos Sidecar --> Prometheus (datos recientes)
                        --> Thanos Store    --> MinIO (datos históricos)
```

**¿Qué es MinIO?**

MinIO es un object storage de alto rendimiento compatible con la API S3 de AWS.
En producción se usaría S3, GCS o Azure Blob. En local MinIO es el equivalente:
almacena los bloques de métricas comprimidos que Thanos sube. Un año de métricas
de un sistema pequeño como este ocupa menos de 1 GB.

**¿Por qué la deduplicación importa?**

Cuando tienes varias instancias de Prometheus (HA setup), cada una scraping los
mismos targets, Thanos Query recibe series duplicadas. Sin deduplicación verías
el doble de datos en los gráficos. Con `--query.replica-label=replica`, Thanos
Query fusiona las series que tienen el mismo `cluster` pero distinto `replica`:

```
Prometheus-1 {cluster="local", replica="prometheus-1"} -> series A
Prometheus-2 {cluster="local", replica="prometheus-2"} -> series B (=A)

Thanos Query deduplica -> devuelve solo una serie sin el label replica
```

En este proyecto solo hay un Prometheus, pero la deduplicación ya está
configurada para cuando añadas una segunda instancia.

**Requisito crítico: deshabilitar compactación en Prometheus**

Thanos exige que Prometheus tenga la compactación local desactivada.
Si Prometheus compacta bloques antes de que Thanos los suba a MinIO,
pueden perderse datos o corromperse bloques. El fix es igualar
`min-block-duration` y `max-block-duration` a `2h`:

```yaml
command:
  - "--storage.tsdb.min-block-duration=2h"
  - "--storage.tsdb.max-block-duration=2h"
```

Con esto Prometheus solo crea bloques de exactamente 2 horas y nunca los
compacta. Thanos asume la responsabilidad de compactar en el object storage.

**Flujo completo de un bloque:**
```
1. Prometheus scraping durante 2h -> crea bloque TSDB local en /prometheus
2. Thanos Sidecar detecta el bloque nuevo
3. Sidecar crea directorio staging /prometheus/thanos
4. Sidecar sube el bloque comprimido a MinIO/thanos/<ulid>/
5. Thanos Store detecta el nuevo bloque en MinIO
6. Thanos Query puede ahora servir datos de ese bloque via Store
7. Después de 7 días Prometheus borra su copia local
8. El bloque sigue disponible via MinIO para siempre (o hasta que lo borres)
```

**¿Qué es el ULID?**

Cada bloque tiene un identificador único llamado ULID (Universally Unique
Lexicographically Sortable Identifier), como `01KSW4VE0R0ZDS07E1BRNV4P2D`.
Son ordenables cronológicamente: los primeros caracteres codifican el timestamp.

**Cambios en `prometheus.yml`:**

Se añaden `external_labels` para identificar la instancia de Prometheus.
El Sidecar los lee y los adjunta a todos los bloques que sube a MinIO:

```yaml
global:
  external_labels:
    cluster: local
    replica: prometheus-1
```

**Grafana apunta a Thanos Query:**

El datasource de Prometheus en Grafana cambia de `http://prometheus:9090`
a `http://thanos-query:10902`. Thanos Query es compatible con la API de
Prometheus, así que todos los dashboards siguen funcionando sin cambios.

**Situación del ecosistema de imágenes MinIO (mayo 2026):**

- MinIO dejó de publicar imágenes en Docker Hub y Quay en octubre 2025
- Bitnami dejará de publicar en AWS ECR Gallery en junio 2026
- La opción más robusta a largo plazo es Chainguard (`cgr.dev/chainguard/minio`)
  que ofrece imágenes gratuitas zero-CVE actualizadas diariamente
- Para este proyecto se usan las últimas imágenes oficiales de Docker Hub
  (`minio/minio:RELEASE.2025-07-23T15-54-02Z`) que siguen siendo accesibles
  y válidas para desarrollo local

**Qué se hace:**
- Añadir `external_labels` a Prometheus (`cluster`, `replica`)
- Deshabilitar compactación local de Prometheus (`min-block-duration=max-block-duration=2h`)
- Levantar MinIO con bucket `thanos` creado automáticamente via `minio-init`
- Thanos Sidecar junto a Prometheus: lee TSDB y sube bloques a MinIO
- Thanos Store Gateway: sirve datos históricos desde MinIO via gRPC
- Thanos Query: federa Sidecar + Store con deduplicación por `replica`
- Grafana apunta a Thanos Query en vez de Prometheus directamente

**Ficheros nuevos/modificados:**
```
thanos/
└── bucket-config.yml           # conexión S3 a MinIO
prometheus/
└── prometheus.yml              # external_labels + compactación desactivada
grafana/provisioning/datasources/
└── prometheus.yml              # url cambia a thanos-query:10902
docker-compose.yml              # minio, minio-init, thanos-sidecar,
                                # thanos-store, thanos-query
```

**Bugs encontrados durante la implementación:**

- Thanos Sidecar rechazaba arrancar porque Prometheus tenía compactación activa
  (`TSDB Max time is 16h48m and Min time is 2h`) -> fix: añadir
  `--storage.tsdb.min-block-duration=2h --storage.tsdb.max-block-duration=2h`
- Thanos Sidecar no podía crear `/prometheus/thanos` para staging de uploads
  por permisos (non-root vs volumen Docker creado como root) -> fix: `user: "0"`
- Thanos Store no podía crear directorio `data` local -> fix: `--data-dir=/tmp/thanos-store`
- `bitnami/minio` y `bitnami/minio-client` ya no son gratuitos en Docker Hub
  ni en AWS ECR Gallery -> fix: usar últimas imágenes de `minio/minio` en Docker Hub

**Al terminar esta fase tendrás:**
```
FastAPI --OTLP--> OTEL Collector --> Prometheus <--> Thanos Sidecar --> MinIO
                                --> Loki                                  |
                                --> Tempo            Thanos Store <-------+
Prometheus --scraping--> Node Exporter / postgres_exporter / cAdvisor
                              Thanos Query (Sidecar + Store)
                                         ↕
                                      Grafana
```

**URLs añadidas:**
| Servicio | URL |
|---|---|
| MinIO Console | http://localhost:9001 (minioadmin/minioadmin) |
| Thanos Query UI | http://localhost:10902 |
| Thanos Stores | http://localhost:10902/stores |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Thanos docs | https://thanos.io/tip/thanos/getting-started.md/ |
| Thanos Sidecar | https://thanos.io/tip/components/sidecar.md/ |
| Thanos Store | https://thanos.io/tip/components/store.md/ |
| Thanos Query | https://thanos.io/tip/components/query.md/ |
| Thanos deduplication | https://thanos.io/tip/thanos/query.md/#deduplication |
| MinIO docs | https://min.io/docs/minio/linux/index.html |
| Chainguard MinIO | https://edu.chainguard.dev/chainguard/chainguard-images/getting-started/minio/ |

YouTube:
- Canal **That DevOps Guy** (Thanos) -> https://www.youtube.com/@MarcelDempers/search?query=thanos
- Canal **Grafana** (Thanos) -> https://www.youtube.com/@Grafana/search?query=thanos


**Conceptos que afianzas aquí:**

Aquí aprendes el patrón sidecar (un proceso que acompaña a otro para extenderlo)
y la separación entre almacenamiento caliente (disco local, rápido, caro, poco
tiempo) y frío (object storage S3, lento, barato, retención larga). Entiendes
que Prometheus no está pensado para guardar años de datos y que la solución no
es un disco más grande, sino delegar en almacenamiento de objetos. Es la base de
cómo escalan en retención Thanos, Mimir y Cortex.

---

### 🟤 Fase 7: Storage persistente para Loki y Tempo en MinIO

**Concepto:** en las fases anteriores Loki y Tempo guardan sus datos en
`/tmp` dentro del contenedor. Cada reinicio borra todo el histórico de logs
y trazas. MinIO ya está levantado desde la Fase 6 para métricas. En esta
fase extendemos su uso para que Loki y Tempo también persistan sus datos en
MinIO, cerrando el círculo del storage distribuido.

**¿Por qué importa?**

Con storage local en `/tmp`:
- Un `docker compose down` borra todos los logs y trazas
- No hay forma de recuperar una traza de hace 3 días
- El volumen de datos está limitado al disco del host sin gestión centralizada

Con MinIO como backend:
- Loki y Tempo sobreviven reinicios
- El storage se gestiona en un único lugar (MinIO Console)
- Preparado para migrar a S3 real en producción cambiando solo la config

**¿Qué es el WAL y por qué necesita volumen persistente?**

El WAL (Write-Ahead Log) es un buffer de escritura en disco que actúa como
seguro ante caídas. Tanto Loki como Tempo escriben los datos entrantes al WAL
antes de procesarlos. Si el proceso cae, el WAL permite recuperar los datos
en vuelo en el próximo arranque.

Sin volumen persistente el WAL vive en el contenedor y se pierde al hacer
`docker compose down`. Con un volumen Docker el WAL sobrevive el reinicio y
Loki/Tempo recuperan los datos que aún no habían llegado a MinIO.

**Flujo completo de un log en Loki con MinIO:**
```
1. OTEL Collector -> Loki ingester
2. Loki escribe en WAL (/loki_wal) para durabilidad inmediata
3. Loki acumula en memoria hasta chunk_idle_period (5 min) o max_chunk_age (10 min)
4. Loki flushea chunk comprimido a MinIO bucket "loki"
5. TSDB shipper sube el índice a MinIO bucket "loki/index/"
6. Tras reinicio: Loki lee el WAL y recupera datos en vuelo
```

**Estructura de buckets en MinIO tras esta fase:**
```
MinIO
├── thanos/         # bloques TSDB de Prometheus (desde Fase 6)
├── loki/
│   ├── fake/       # chunks de logs (tenant "fake" con auth_enabled: false)
│   ├── index/      # índice TSDB
│   └── loki_cluster_seed.json
└── tempo/          # bloques de trazas compilados
```

**Qué se hace:**
- Añadir buckets `loki` y `tempo` en `minio-init`
- Actualizar `loki/loki-config.yml` para usar S3/MinIO como backend
- Actualizar `tempo/tempo-config.yml` para usar S3/MinIO como backend
- Añadir volúmenes persistentes para WAL de Loki y Tempo
- Añadir `user: "0"` a Loki y Tempo por permisos de volumen

**Ficheros modificados:**
```
docker-compose.yml          # minio-init añade buckets loki y tempo,
                            # loki y tempo con user:0 y volúmenes WAL
loki/loki-config.yml        # backend S3, flush config, WAL path
tempo/tempo-config.yml      # backend S3
```

**Config Loki con MinIO:**

La clave es usar el formato de endpoint explícito con `insecure: true` en vez
de la URL `s3://`. El SDK de AWS que usa Loki trata el endpoint como región si
usas el formato URL, lo que causa un HTTP 301 PermanentRedirect de MinIO:

```yaml
storage_config:
  aws:
    endpoint: minio:9000        # no usar s3://user:pass@host/bucket
    bucketnames: loki
    access_key_id: minioadmin
    secret_access_key: minioadmin
    s3forcepathstyle: true
    region: us-east-1           # valor ficticio pero requerido por el SDK
    insecure: true              # HTTP en vez de HTTPS
```

Configuración del ingester para flush frecuente (por defecto son 30 min y 2h,
demasiado para un entorno de aprendizaje):

```yaml
ingester:
  chunk_idle_period: 5m
  max_chunk_age: 10m
  chunk_retain_period: 1m
  wal:
    dir: /loki_wal
    enabled: true
```

**Config Tempo con MinIO:**

Tempo 3.0 con S3 es más directo que Loki. Solo cambia el backend:

```yaml
storage:
  trace:
    backend: s3
    s3:
      bucket: tempo
      endpoint: minio:9000
      access_key: minioadmin
      secret_key: minioadmin
      insecure: true
    wal:
      path: /tmp/tempo/wal
```

**Bugs encontrados durante la implementación:**

- Loki con URL `s3://user:pass@minio:9000/loki` causaba HTTP 301 PermanentRedirect
  porque el SDK de AWS extrae `minio:9000` como nombre de región -> fix: usar
  endpoint explícito con `insecure: true` y `region: us-east-1`
- Montar volumen en `/tmp/loki/wal` hacía que Docker creara `/tmp/loki` como
  root, rompiendo los permisos del proceso Loki (uid 10001) para `/tmp/loki/tsdb-cache`
  -> fix: mover el WAL a `/loki_wal` (ruta raíz, fuera de `/tmp/loki`)
- Volumen Docker creado como root no escribible por Loki y Tempo -> fix: `user: "0"`
  en ambos servicios, igual que Thanos Sidecar y Store en Fase 6

**Al terminar esta fase tendrás:**
```
FastAPI --OTLP--> OTEL Collector --> Loki  --> MinIO (bucket loki)
                               --> Tempo --> MinIO (bucket tempo)
                               --> Prometheus <--> Thanos --> MinIO (bucket thanos)
                                              Grafana
```

Los tres pilares del stack de observabilidad (métricas, logs, trazas) persisten
en MinIO y sobreviven reinicios del stack completo.

**URLs sin cambios:** las mismas de las fases anteriores.

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Loki S3 storage | https://grafana.com/docs/loki/latest/configure/storage/ |
| Loki ingester config | https://grafana.com/docs/loki/latest/configure/#ingester |
| Tempo S3 storage | https://grafana.com/docs/tempo/latest/configuration/s3/ |
| Loki WAL | https://grafana.com/docs/loki/latest/operations/storage/wal/ |


**Conceptos que afianzas aquí:**

Refuerzas la idea de durabilidad: el WAL (Write-Ahead Log) protege los datos en
vuelo ante una caída antes de que lleguen al almacenamiento definitivo. Entiendes
que persistencia y retención son problemas distintos, y que dar a las tres
señales el mismo backend de object storage (MinIO) unifica la gestión. También
ves que la API S3 es el estándar de facto: el mismo concepto sirve para AWS S3,
MinIO, Ceph o R2 cambiando solo el endpoint.

---

### ⚪ Fase 8: Alertas con Prometheus Alertmanager

**Concepto:** tener métricas sin alertas es solo la mitad del trabajo. En
producción necesitas saber cuándo algo va mal sin estar mirando dashboards
constantemente. Alertmanager recibe las alertas que dispara Prometheus y
las enruta a los canales de notificación configurados.

**¿Qué es Alertmanager?**

Alertmanager recibe alertas de Prometheus y aplica lógica de enrutamiento.
El flujo es:

```
Prometheus --evalúa reglas cada 15s--> condición cumplida durante "for"
           --> alerta FIRING --> Alertmanager
                                      |
                         +------------+------------+
                         |                         |
                      agrupa                    silencia
                      inhibe                    enruta
                         |
                    Webhook/Slack/Email/PagerDuty
```

La separación entre Prometheus y Alertmanager es intencional. Prometheus
decide cuándo una alerta existe. Alertmanager decide a quién se le notifica,
cómo se agrupa y cuándo se repite. Esta separación permite gestionar la
"alerta fatiga" sin tocar las reglas de detección.

**Conceptos clave de Alertmanager:**

`group_by`: agrupa alertas relacionadas en una sola notificación. Si caen
5 instancias a la vez, recibes 1 notificación, no 5.

`group_wait`: tiempo que espera antes de enviar la primera notificación del
grupo. Da margen para que lleguen alertas relacionadas y agruparlas.

`repeat_interval`: cada cuánto se reenvía la notificación si la alerta sigue
activa. Evita spam pero garantiza que no se olvide un incidente.

`inhibit_rules`: suprime alertas secundarias cuando hay una primaria activa.
Si el servidor está caído (`critical`), no tiene sentido recibir también las
alertas de latencia alta (`warning`) del mismo servidor.

**¿Qué es el blackbox_exporter?**

Es un exporter de Prometheus para probar endpoints externos via HTTP, TCP,
ICMP o DNS. No scraping métricas de la app: comprueba si el endpoint responde.
La métrica clave es `probe_success=1` (OK) o `probe_success=0` (KO).

Sin blackbox_exporter, `up{job="payment-api"}` no existe porque la app usa
OTLP (push) en vez de scraping. El blackbox_exporter convierte un endpoint
HTTP en un target de Prometheus.

**Qué se hace:**
- Levantar Alertmanager con config de enrutamiento a webhook
- Levantar blackbox_exporter para probar el endpoint `/health` de la API
- Añadir scrape job para el blackbox_exporter en Prometheus con relabeling
- Habilitar `--web.enable-lifecycle` en Prometheus para reloads sin restart
- Definir reglas de alerta en `prometheus/rules/payment-api.yml`
- Verificar el flujo completo parando la API

**Ficheros nuevos/modificados:**
```
alertmanager/
└── alertmanager.yml            # config de enrutamiento y receivers
prometheus/rules/
└── payment-api.yml             # reglas de alerta
prometheus/prometheus.yml       # añade alerting, rule_files y blackbox scrape
docker-compose.yml              # añade alertmanager y blackbox-exporter
```

**Reglas de alerta implementadas:**

```yaml
- alert: PaymentApiDown
  expr: probe_success{job="payment-api"} == 0
  for: 1m
  labels:
    severity: critical

- alert: OtelCollectorDown
  expr: up{job="otel-collector"} == 0
  for: 1m
  labels:
    severity: critical

- alert: PrometheusTargetDown
  expr: up == 0
  for: 2m
  labels:
    severity: warning

- alert: HighPaymentLatency
  expr: |
    histogram_quantile(0.99,
      sum by (le) (
        rate(http_server_request_duration_seconds_bucket{
          http_route="/payments",
          http_request_method="POST"
        }[5m])
      )
    ) > 0.5
  for: 2m
  labels:
    severity: warning
```

**Scrape job para el blackbox_exporter:**

El relabeling convierte la dirección del target en el parámetro `target`
del probe, y apunta el scrape al blackbox_exporter:

```yaml
- job_name: payment-api
  metrics_path: /probe
  params:
    module: [http_2xx]
  static_configs:
    - targets: [http://payment-api:8000/health]
  relabel_configs:
    - source_labels: [__address__]
      target_label: __param_target
    - source_labels: [__param_target]
      target_label: instance
    - target_label: __address__
      replacement: blackbox-exporter:9115
```

**Payload de notificación recibido (ejemplo real):**

```json
{
  "receiver": "webhook",
  "status": "resolved",
  "alerts": [{
    "status": "resolved",
    "labels": {
      "alertname": "PaymentApiDown",
      "cluster": "local",
      "instance": "http://payment-api:8000/health",
      "job": "payment-api",
      "replica": "prometheus-1",
      "severity": "critical"
    },
    "annotations": {
      "description": "El servicio payment-api lleva más de 1 minuto sin responder.",
      "summary": "Payment API caída"
    },
    "startsAt": "2026-06-12T11:47:49.014Z",
    "endsAt": "2026-06-12T12:09:34.014Z"
  }],
  "notification_reason": "all alerts resolved"
}
```

**Bugs encontrados durante la implementación:**

- `up{job="payment-api"}` no existe porque la app usa OTLP (push), no scraping
  -> fix: añadir blackbox_exporter y usar `probe_success` en vez de `up`
- El scrape job del blackbox con `metrics_path: /health` fallaba con
  `received unsupported Content-Type "application/json"` porque `/health`
  devuelve JSON, no formato Prometheus -> fix: usar el blackbox_exporter
  correctamente con relabeling
- Añadir `fallback_scrape_protocol: PrometheusText0.0.4` tampoco funcionó
  porque el JSON simplemente no se parsea como Prometheus text
- Alertmanager 0.32.2 loguea notificaciones a nivel DEBUG, no INFO -> sin
  `--log.level=debug` parece que no envía nada cuando en realidad sí lo hace
- `curl -X POST http://localhost:9093/-/reload` devuelve 404 si no se añade
  `--web.enable-lifecycle` al comando de Alertmanager
- webhook.site mostraba "Waiting for the first request" aunque las
  notificaciones llegaban correctamente -> era un problema de la UI de
  webhook.site, no del stack. Los logs de Alertmanager confirman los envíos:
  `msg="Notify success" attempts=1 duration=~240ms`

**URLs añadidas:**

| Servicio | URL |
|---|---|
| Alertmanager UI | http://localhost:9093 |
| Blackbox Exporter | http://localhost:9115 |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Alertmanager docs | https://prometheus.io/docs/alerting/latest/alertmanager/ |
| Alerting rules | https://prometheus.io/docs/prometheus/latest/configuration/alerting_rules/ |
| Blackbox exporter | https://github.com/prometheus/blackbox_exporter |
| Blackbox config | https://github.com/prometheus/blackbox_exporter/blob/master/CONFIGURATION.md |
| Alertmanager routing | https://prometheus.io/docs/alerting/latest/configuration/#route |

YouTube:
- Canal **That DevOps Guy** (Alertmanager) -> https://www.youtube.com/@MarcelDempers/search?query=alertmanager
- Canal **Grafana** (Alerting) -> https://www.youtube.com/@Grafana/search?query=alerting


**Conceptos que afianzas aquí:**

La idea central es la separación de responsabilidades: Prometheus detecta
(evalúa reglas), Alertmanager decide qué hacer (agrupa, silencia, inhibe,
enruta). Aprendes que `up` no es salud real y por qué hace falta el blackbox
exporter para probar un endpoint que usa push. Internalizas conceptos de gestión
de alertas (group_wait, repeat_interval, inhibición) que existen para combatir
la fatiga de alertas, el enemigo silencioso de cualquier equipo de guardia.

---

### 🔵 Fase 9: Segunda instancia de Prometheus y deduplicación real con Thanos

**Concepto:** en la Fase 6 configuramos la deduplicación de Thanos con
`replica="prometheus-1"` pero nunca la probamos de verdad porque solo había
una instancia de Prometheus. En esta fase levantamos una segunda instancia
(`replica="prometheus-2"`) scrapeando los mismos targets, configuramos el
Collector para enviar métricas a ambas instancias, y verificamos que Thanos
Query deduplica correctamente y que el stack sobrevive la caída de una instancia.

**¿Por qué dos instancias de Prometheus?**

En producción se usan dos instancias idénticas scrapeando los mismos targets
para garantizar alta disponibilidad. Si una cae, la otra sigue recogiendo
métricas. Sin Thanos, Grafana vería series duplicadas. Con Thanos Query y
`--query.replica-label=replica`, las series con el mismo `cluster` pero
distinto `replica` se fusionan en una sola.

**Flujo completo con HA:**
```
OTEL Collector --remote write--> prometheus:9090   (replica=prometheus-1)
               --remote write--> prometheus-2:9090 (replica=prometheus-2)

prometheus   ---> thanos-sidecar   ---> MinIO
prometheus-2 ---> thanos-sidecar-2 ---> MinIO

Grafana --> Thanos Query --> deduplica por replica --> 1 serie limpia
                        --> Thanos Store (histórico)
```

**Si cae `prometheus-1`:**
```
OTEL Collector --remote write falla--> prometheus:9090   (DOWN)
               --remote write OK  --> prometheus-2:9090 (UP)

Grafana --> Thanos Query --> thanos-sidecar (DOWN, ignorado)
                        --> thanos-sidecar-2 (UP, sirve datos)
                        --> Thanos Store (histórico)
                        --> datos disponibles sin interrupción
```

**Remote write dual en el OTEL Collector:**

Para que las métricas OTel sobrevivan la caída de una instancia, el Collector
debe enviar a ambas. Se usan dos exporters con nombre compuesto:

```yaml
exporters:
  prometheusremotewrite/p1:
    endpoint: http://prometheus:9090/api/v1/write
  prometheusremotewrite/p2:
    endpoint: http://prometheus-2:9090/api/v1/write

service:
  pipelines:
    metrics:
      exporters: [prometheusremotewrite/p1, prometheusremotewrite/p2]
```

**Qué se hace:**
- Crear `prometheus/prometheus2.yml` con `replica: prometheus-2`
- Añadir `prometheus-2` con volumen propio `prometheus2_data`
- Añadir `thanos-sidecar-2` apuntando a `prometheus-2`
- Actualizar `thanos-query` con el nuevo endpoint `--endpoint=thanos-sidecar-2:10901`
- Actualizar `otel-collector-config.yml` con remote write dual
- Verificar deduplicación: `Result series: 1` sin label `replica` en Grafana
- Verificar HA: parar `prometheus-1` y confirmar que los datos siguen disponibles

**Ficheros nuevos/modificados:**
```
prometheus/
└── prometheus2.yml             # config segunda instancia
docker-compose.yml              # prometheus-2, thanos-sidecar-2, volumen prometheus2_data
                                # thanos-query con --endpoint=thanos-sidecar-2:10901
otel-collector/
└── otel-collector-config.yml   # prometheusremotewrite/p1 y /p2
```

**Verificación de deduplicación en Grafana:**

Con ambas instancias activas, `payments_created_total` devuelve:
- `Result series: 1` — una sola serie ✅
- Labels: `cluster="local"` sin `replica` ✅
- Thanos Query eliminó el label `replica` al deduplicar

Sin deduplicación aparecerían dos series:
```
payments_created_total{cluster="local", replica="prometheus-1"} 26
payments_created_total{cluster="local", replica="prometheus-2"} 26
```

**Verificación de HA:**

```bash
# Para prometheus-1 y su sidecar
docker compose stop prometheus thanos-sidecar

# Las métricas scrapeadas (infraestructura) siguen disponibles via prometheus-2
curl -s "http://localhost:10902/api/v1/query?query=node_cpu_seconds_total" | \
  python3 -m json.tool | grep value | head -2

# Las métricas OTel también (gracias al remote write dual)
curl -s "http://localhost:10902/api/v1/query?query=payments_created_total" | \
  python3 -m json.tool | grep value

# Levanta de nuevo
docker compose start prometheus thanos-sidecar
```

**URLs añadidas:**

| Servicio | URL |
|---|---|
| Prometheus-2 UI | http://localhost:9091 |
| Thanos Stores | http://localhost:10902/stores (muestra 2 sidecars) |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| Thanos HA setup | https://thanos.io/tip/thanos/quick-tutorial.md/#ha-prometheus-with-thanos |
| Thanos deduplication | https://thanos.io/tip/thanos/query.md/#deduplication |
| Prometheus HA | https://prometheus.io/docs/introduction/faq/#can-prometheus-be-made-highly-available |


**Conceptos que afianzas aquí:**

Esta fase fija el concepto de alta disponibilidad por redundancia: dos
Prometheus scrapeando lo mismo, y deduplicación por el label `replica` para que
el usuario vea una sola serie. Entiendes que la deduplicación es lo que hace
viable correr réplicas sin duplicar todo en los dashboards. Es el patrón que
sostiene cualquier despliegue serio de Prometheus en producción, donde perder
métricas durante un reinicio no es aceptable.

---

### 🟢 Fase 10: Segundo servicio y trazas distribuidas

**Concepto:** hasta ahora todas las trazas tienen un único servicio:
`payment-api`. En producción los sistemas están formados por múltiples
servicios que se llaman entre sí. Una traza distribuida recorre todos estos
servicios con el mismo `trace_id`, permitiendo ver el flujo completo de una
petición de extremo a extremo.

**¿Qué es el context propagation?**

Cuando `payment-api` llama a `fraud-service`, incluye el `trace_id` y `span_id`
actuales en la cabecera HTTP `traceparent` (estándar W3C Trace Context).
`fraud-service` la lee, crea un span hijo con el mismo `trace_id`, y lo envía
al mismo Collector. Tempo reconstruye el árbol completo:

```
payment-api: POST /payments (31ms)
  └── payment.process (28ms)
       └── fraud-service: POST /check (179µs)   <- span de otro servicio
            └── fraud.check (47µs)
       └── INSERT paymentsdb
       └── SELECT paymentsdb
```

**Formato W3C traceparent:**
```
traceparent: 00-{trace-id}-{parent-span-id}-{flags}
             00-7f13205682da8831cd2c269f13c80c1b-4cd3cab2376d9bed-01
```

**¿Qué se hace:**
- Crear `fraud-service/` en Go con OTel SDK, HTTP server y lógica de negocio
- `payment-api` inyecta el contexto en los headers via `inject(headers)` e
  invoca al fraud-service con `httpx` antes de persistir el pago
- Pagos > 1000€ son rechazados con HTTP 422
- `fraud-service` extrae el contexto del header `traceparent` y crea spans hijo
- Ambos envían trazas al mismo OTEL Collector

**Flujo distribuido:**
```
Cliente --> payment-api (Python) --traceparent header--> fraud-service (Go)
               |                          |
           OTLP gRPC                  OTLP gRPC
               |                          |
               +----------> OTEL Collector --> Tempo
                                              (trace_id compartido)
```

**Ficheros nuevos/modificados:**
```
fraud-service/
├── main.go         # HTTP server + OTel Go SDK + lógica de fraude
├── go.mod          # dependencias Go
└── Dockerfile      # multi-stage: golang:1.24-alpine -> alpine:3.21
app/
├── app.py          # añade llamada a fraud-service con propagación de contexto
└── requirements.txt # añade httpx==0.28.1
docker-compose.yml  # añade servicio fraud-service
```

**Propagación del contexto en Python (`app.py`):**

```python
from opentelemetry.propagate import inject
import httpx

with tracer.start_as_current_span("payment.process") as span:
    headers = {}
    inject(headers)  # inyecta traceparent con el trace_id actual
    fraud_resp = httpx.post(
        f"{fraud_url}/check",
        json={"amount": payment.amount, "currency": payment.currency, "payment_id": ""},
        headers=headers,
        timeout=5.0,
    )
    if fraud_resp.json().get("status") == "rejected":
        raise HTTPException(status_code=422, detail=f"Pago rechazado: {fraud_resp.json().get('reason')}")
```

**Extracción del contexto en Go (`fraud-service/main.go`):**

El SDK de OTel Go (via `otel.GetTextMapPropagator().Extract()`) no extraía
correctamente el contexto remoto del header en la versión descargada por
`go mod tidy`. El fix fue parsear el header `traceparent` manualmente y
crear el span context con `trace.ContextWithRemoteSpanContext`:

```go
func extractRemoteContext(ctx context.Context, r *http.Request) context.Context {
    tp := r.Header.Get("Traceparent")
    parts := strings.Split(tp, "-")
    if len(parts) != 4 { return ctx }

    traceID, _ := trace.TraceIDFromHex(parts[1])
    spanID, _ := trace.SpanIDFromHex(parts[2])

    sc := trace.NewSpanContext(trace.SpanContextConfig{
        TraceID:    traceID,
        SpanID:     spanID,
        TraceFlags: trace.FlagsSampled,
        Remote:     true,
    })

    return trace.ContextWithRemoteSpanContext(ctx, sc)
}

func checkHandler(w http.ResponseWriter, r *http.Request) {
    ctx := extractRemoteContext(r.Context(), r)
    tracer := otel.Tracer("fraud-service")
    ctx, span := tracer.Start(ctx, "POST /check")
    defer span.End()
    ...
}
```

**Dockerfile multi-stage para Go:**

Sin Go instalado en el host, el build se hace completamente en Docker.
El `go mod tidy` se ejecuta con todos los ficheros copiados para que pueda
analizar los imports y generar el `go.sum`:

```dockerfile
FROM golang:1.24-alpine AS builder
WORKDIR /app
COPY . .
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o fraud-service .

FROM alpine:3.21
RUN apk add --no-cache ca-certificates
WORKDIR /app
COPY --from=builder /app/fraud-service .
EXPOSE 8001
CMD ["./fraud-service"]
```

**¿Por qué Go?**

Go es el lenguaje del ecosistema de plataforma e infraestructura: Kubernetes,
Prometheus, Grafana, Thanos, Loki, Tempo y el OTEL Collector están escritos
en Go. Practicar OTel con Go es directamente aplicable al trabajo del día a día.

**Bugs encontrados durante la implementación:**

- `go.mod` incluía `go.opentelemetry.io/otel/semconv/v1.26.0` como módulo
  separado -> fix: es un paquete dentro de `go.opentelemetry.io/otel`, no
  un módulo independiente, eliminar del `require`
- `go mod download` no genera `go.sum` sin el `main.go` -> fix: copiar todos
  los ficheros primero y ejecutar `go mod tidy` en vez de `go mod download`
- `otel.GetTextMapPropagator().Extract()` devolvía contexto vacío
  (`trace_id=000...000 valid=false`) aunque el header `traceparent` llegaba
  correctamente -> fix: parsear el header W3C manualmente con
  `trace.TraceIDFromHex`, `trace.SpanIDFromHex` y
  `trace.ContextWithRemoteSpanContext`
- `httpx` no estaba en `requirements.txt` de la app -> fix: añadir
  `httpx==0.28.1`

**Resultado en Grafana Tempo:**

Una sola traza con `Services: 2` muestra el árbol completo con spans de
`payment-api` y `fraud-service` identificados por colores distintos.
El `trace_id` es el mismo en ambos servicios.

**URLs añadidas:**

| Servicio | URL |
|---|---|
| Fraud Service health | http://localhost:8001/health |

**Para profundizar:**

| Recurso | Enlace |
|---|---|
| OTel Go SDK | https://opentelemetry.io/docs/languages/go/ |
| W3C Trace Context | https://www.w3.org/TR/trace-context/ |
| Context propagation | https://opentelemetry.io/docs/concepts/context-propagation/ |
| trace.ContextWithRemoteSpanContext | https://pkg.go.dev/go.opentelemetry.io/otel/trace#ContextWithRemoteSpanContext |
| Distributed tracing patterns | https://opentelemetry.io/docs/concepts/signals/traces/ |

YouTube:
- Canal **opentelemetry** oficial (Go) -> https://www.youtube.com/@otel-official/search?query=go
- Canal **Grafana** (distributed tracing) -> https://www.youtube.com/@Grafana/search?query=distributed+tracing


**Conceptos que afianzas aquí:**

Aquí cierras el círculo de las trazas distribuidas: entiendes el context
propagation y el estándar W3C `traceparent` como el mecanismo que cose una
petición a través de servicios y lenguajes distintos (Python y Go compartiendo
un `trace_id`). Aprendes que la instrumentación cruza fronteras de lenguaje
gracias a un protocolo común, y vives de primera mano que los detalles de
implementación importan (el bug de extracción de contexto). Es el concepto que
hace observable una arquitectura de microservicios real.

---

<a name="7-correlacion-entre-las-tres-senales"></a>
## 🔗 7. Correlación entre las tres señales

El campo `trace_id` es el hilo conductor de las 3 señales en Grafana:

```
1. Alerta en Prometheus -> CPU alta en /payments
           ↓
2. Grafana -> buscar logs de ese timestamp en Loki
           ↓
3. El log contiene trace_id -> abrir esa traza en Tempo
           ↓
4. La traza muestra qué span tardó -> causa raíz identificada
```

---

<a name="8-metricas-expuestas-por-la-api"></a>
## 📊 8. Métricas expuestas por la API

**Fases 2-4** (via `prometheus-fastapi-instrumentator` + `prometheus_client`, endpoint `/metrics`):

| Métrica | Tipo | Descripción |
|---|---|---|
| `http_requests_total` | Counter | Requests por endpoint y status (auto) |
| `http_request_duration_seconds` | Histogram | Latencia HTTP (auto) |
| `payments_created_total` | Counter | Pagos creados por moneda (custom) |
| `payments_amount_euros` | Histogram | Distribución de importes (custom) |

**Fase 5 en adelante** (via OTel metrics SDK + OTLP al Collector + remote write a Prometheus):

| Métrica | Tipo | Labels | Descripción |
|---|---|---|---|
| `payments_created_total` | Counter | `currency`, `job`, `otel_scope_name` | Pagos creados (custom) |
| `payments_amount_euros` | Histogram | `currency`, `job`, `otel_scope_name` | Distribución de importes (custom) |
| `http.server.request.duration` | Histogram | `http.method`, `http.route`, `http.status_code` | Latencia HTTP (auto via FastAPIInstrumentor) |

El endpoint `/metrics` desaparece en la Fase 5. Las métricas se envían via OTLP
al Collector, que las reenvía a Prometheus via remote write. La métrica
`payments_created_total` aparece en Prometheus con labels adicionales `job`
(derivado de `service.name`) y `otel_scope_name` (el módulo que crea el meter).

---

<a name="9-campos-de-log"></a>
## 🏷️ 9. Campos de log (Fase 3 en adelante)

```json
{
  "timestamp": "2025-01-15T10:30:00Z",
  "level": "info",
  "service": "payment-api",
  "endpoint": "/payments",
  "method": "POST",
  "status_code": 201,
  "payment_id": "550e8400-e29b-41d4-a716-446655440000",
  "amount": 99.99,
  "currency": "EUR",
  "duration_ms": 45,
  "trace_id": "abc123def456",
  "span_id": "789xyz"
}
```

---

<a name="10-notas-importantes"></a>
## ⚠️ 10. Notas importantes

- **Cada fase es acumulativa**: el `docker-compose.yml` crece en cada fase añadiendo servicios.
- **Todos los contenedores tienen `mem_limit`**: para no comprometer los 16 GB del equipo.
- **Thanos Compact se omite en local**: solo tiene sentido con semanas de datos históricos reales.

---

<a name="11-dependabot"></a>
## 🤖 11. Dependabot

Dependabot revisa automáticamente las dependencias del proyecto cada semana y abre
Pull Requests cuando hay versiones nuevas disponibles. Está configurado en
`.github/dependabot.yml` y cubre estos ecosistemas:

| Ecosistema | Qué monitoriza |
|---|---|
| `pip` | `app/requirements.txt` y `requirements-dev.txt` |
| `docker` | Imagen base del `app/Dockerfile` |
| `docker-compose` | Imágenes del `docker-compose.yml` |
| `github-actions` | Versiones de las Actions del workflow de CI (ej. `actions/checkout@v6`) |

Cada semana Dependabot abre PRs automáticos con las actualizaciones disponibles.
El test gate del CI se ejecuta sobre cada PR, de forma que solo se mergea
lo que pasa los tests.

**Limitación importante:** el ecosistema `github-actions` solo monitoriza las versiones
de las Actions, no las imágenes Docker referenciadas como servicios dentro de los workflows
(por ejemplo `postgres:18-alpine` en el job `test`). Esas imágenes hay que revisarlas
y actualizarlas a mano cuando se actualice la imagen principal de PostgreSQL.

---

<a name="12-validaciones"></a>
## ✅ 12. Validaciones del stack

Este apartado recoge los comandos de validación del stack completo. Úsalos
siempre que actualices una imagen, mergees un PR de Dependabot, o simplemente
quieras verificar que todo funciona después de un reinicio.

**Regla general para actualizaciones de imagen:** antes de mergear un PR de
Dependabot o cambiar una imagen manualmente, prueba siempre en local con
`docker compose up -d --force-recreate <servicio>` y ejecuta los checks
correspondientes a ese servicio. El CI solo valida que la API arranca y pasa
los tests de Python; no valida la configuración del stack de observabilidad.

---

### Stack

```bash
# Estado de todos los contenedores
docker compose ps --format "table {{.Name}}\t{{.Status}}"

# Errores críticos en los últimos 5 minutos
# Ignora level=info porque algunos servicios loguean "error" como parte de mensajes informativos
docker compose logs --since 5m 2>&1 | grep -iE "error|fatal|panic" | grep -v "level=info"
```

Todos los contenedores deben estar `Up`. Errores transitorios de conexión
durante reinicios son normales y se resuelven solos.

---

### Métricas

```bash
# Genera un pago de prueba
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 99.99, "currency": "EUR"}' > /dev/null

sleep 20

# Verifica que la métrica llega a Prometheus via Thanos Query
# Debe devolver cluster="local" y job="payment-api"
curl -s "http://localhost:10902/api/v1/query?query=payments_created_total" | \
  python3 -m json.tool | grep -E "cluster|job|value"

# Verifica que Node Exporter llega (scraping directo de Prometheus)
curl -s "http://localhost:10902/api/v1/query?query=node_cpu_seconds_total{mode='idle'}" | \
  python3 -m json.tool | grep -E "instance|job" | head -4

# Verifica métricas internas del OTEL Collector
curl -s http://localhost:8888/metrics | grep "otelcol_receiver_accepted" | head -3
```

Si `payments_created_total` no aparece, el Collector no está recibiendo datos
de la app. Revisa `docker compose logs otel-collector --tail 20`.

Si `node_cpu_seconds_total` no aparece, Prometheus no está scrapeando Node
Exporter. Revisa los targets en `http://localhost:9090/targets`.

---

### Logs

```bash
# Verifica que Loki recibe logs con el label service_name="payment-api"
# Debe devolver "stream" y "values" en la respuesta
curl -s --get \
  --data-urlencode 'query={service_name="payment-api"}' \
  --data-urlencode 'limit=1' \
  'http://localhost:3100/loki/api/v1/query_range' | \
  python3 -m json.tool | grep -E '"stream"|"values"' | head -4

# Verifica el label index de Loki (debe incluir service_name)
curl -s 'http://localhost:3100/loki/api/v1/labels' | python3 -m json.tool | grep service_name
```

Si Loki no tiene logs, el OTEL Collector no está exportando a Loki. Revisa
`docker compose logs otel-collector --tail 20` buscando errores en el pipeline
de logs.

---

### Trazas

```bash
# Genera tráfico y verifica que Tempo recibe trazas
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 25.00, "currency": "EUR"}' > /dev/null

sleep 3

# Busca trazas recientes (Tempo 3.0+)
curl -s 'http://localhost:3200/api/search' | \
  python3 -m json.tool | grep -E "traceID|rootServiceName"

# Verifica que Tempo está listo
curl -s http://localhost:3200/ready
```

Debe devolver `ready` y una lista de trazas con `rootServiceName: payment-api`.

En Tempo 3.0 el `backend_scheduler` loga `no jobs found` periódicamente cuando
no hay bloques pendientes de compactar. Es ruido esperado, no un error real.

---

### Thanos

```bash
# Verifica que Thanos Query ve tanto el Sidecar como el Store
# Debe aparecer thanos-sidecar:10901 (Sidecar) y thanos-store:10901 (Store)
curl -s http://localhost:10902/api/v1/stores | python3 -m json.tool | grep -E "name|healthy"

# Verifica que el Sidecar tiene los external_labels correctos
# Debe mostrar cluster="local" y replica="prometheus-1"
docker compose logs thanos-sidecar --tail 5 | grep "external_labels"

# Verifica que MinIO tiene bloques subidos
curl -s "http://localhost:10902/api/v1/query?query=thanos_shipper_uploads_total" | \
  python3 -m json.tool | grep value
```

Si el Sidecar no aparece en Stores, revisa que Prometheus tiene `external_labels`
y `min-block-duration=max-block-duration=2h` en su comando.

---

### Correlación traza-log

Esta validación verifica que el flujo completo funciona: genera un pago, copia
su `trace_id` desde Grafana Tempo y busca el log correspondiente en Loki.

```bash
# 1. Genera un pago
curl -s -X POST http://localhost:8000/payments \
  -H "Content-Type: application/json" \
  -d '{"amount": 200.00, "currency": "EUR"}'
# Copia el payment_id de la respuesta

# 2. En Grafana: Explore -> Tempo -> busca trazas recientes
#    Abre la traza -> icono de logs -> debe abrir Loki con el trace_id
#    La query generada debe usar {service_name="payment-api"}

# 3. Validación via API directa con un trace_id conocido
TRACE_ID=$(curl -s 'http://localhost:3200/api/search' | \
  python3 -c "import sys,json; print(json.load(sys.stdin)['traces'][0]['traceID'])")

echo "trace_id: $TRACE_ID"

curl -s --get \
  --data-urlencode "{service_name=\"payment-api\"} | json | trace_id=\"$TRACE_ID\"" \
  --data-urlencode 'limit=1' \
  'http://localhost:3100/loki/api/v1/query_range' | \
  python3 -m json.tool | grep -E "trace_id|payment_id" | head -4
```

---

### Alertmanager y alertas

```bash
# Reglas cargadas y su estado
curl -s http://localhost:9090/api/v1/rules | \
  python3 -m json.tool | grep -E "name|state|health"

# Alertas activas en Prometheus
curl -s http://localhost:9090/api/v1/alerts | \
  python3 -m json.tool | grep -E "alertname|state"

# Prometheus ve Alertmanager
curl -s http://localhost:9090/api/v1/alertmanagers | python3 -m json.tool

# Alertas activas en Alertmanager
curl -s http://localhost:9093/api/v2/alerts | \
  python3 -m json.tool | grep -E "alertname|state"

# Prometheus envió notificaciones sin errores
curl -s http://localhost:9090/metrics | \
  grep -E "prometheus_notifications_sent_total|prometheus_notifications_errors_total"

# Blackbox probe de la API
curl -s "http://localhost:9115/probe?target=http://payment-api:8000/health&module=http_2xx" | \
  grep probe_success
```

Para probar el flujo completo:
```bash
# 1. Para la API
docker compose stop api

# 2. Espera 1 minuto y verifica que PaymentApiDown está firing
curl -s http://localhost:9090/api/v1/alerts | \
  python3 -m json.tool | grep -E "alertname|state"

# 3. Verifica que Alertmanager envió el webhook (--log.level=debug en alertmanager)
docker compose logs alertmanager | grep "Notify success"

# 4. Levanta la API
docker compose start api

# 5. Verifica notificación de resolución
docker compose logs alertmanager | grep "resolved"
```

---

Cuando Dependabot abre un PR o cambias una imagen manualmente, sigue este flujo:

```bash
# 1. Actualiza la imagen en docker-compose.yml y levanta solo ese servicio
docker compose up -d --force-recreate <servicio>

# 2. Espera que arranque y verifica logs
sleep 10
docker compose logs <servicio> --tail 20

# 3. Ejecuta el check específico del servicio según la tabla:
#
# Servicio              Check principal
# prometheus            curl -s http://localhost:9090/-/ready
# grafana               curl -s http://localhost:3000/api/health
# loki                  curl -s http://localhost:3100/ready
# tempo                 curl -s http://localhost:3200/ready
# otel-collector        curl -s http://localhost:8888/metrics | head -3
# thanos-query          curl -s http://localhost:10902/-/ready
# thanos-sidecar        docker compose logs thanos-sidecar --tail 5
# thanos-store          docker compose logs thanos-store --tail 5
# minio                 curl -s http://localhost:9000/minio/health/live

# 4. Si el servicio arranca, verifica el pipeline end-to-end:
#    Genera un pago y comprueba que la métrica, log y traza llegan a Grafana

# 5. Si algo falla, revierte la imagen:
docker compose up -d --force-recreate <servicio>  # con la imagen anterior en docker-compose.yml
```

---

<a name="13-gotchas"></a>
## 🐛 13. Errores conceptuales comunes (gotchas)

Errores de comprensión que casi todo el mundo comete al aprender observabilidad.
Identificarlos ahorra horas de debugging y malas decisiones de diseño.

### Meter alta cardinalidad en labels de métricas

El error más caro. Poner `user_id`, `trace_id`, `email`, `IP` o `request_id`
como label de una métrica de Prometheus. Cada valor único crea una serie
temporal nueva y revienta la memoria del servidor. Esos datos van en logs o
trazas, nunca en labels de métricas. Si necesitas correlacionar una métrica
con una petición concreta, eso son los `exemplars`, no un label.

### Confundir logs con trazas

Un log es un evento puntual ("pago creado, id=X"). Una traza es el recorrido
completo de una petición a través de servicios, con tiempos por salto. No
intentes reconstruir el flujo de una petición leyendo logs y correlacionando
timestamps a mano: para eso existen las trazas. Y al revés, no metas en una
traza el detalle textual de cada cosa que pasa: para eso están los logs.
Se complementan via `trace_id`.

### Pensar que más datos = mejor observabilidad

Loguear todo a nivel DEBUG en producción, crear cientos de métricas "por si
acaso", trazar el 100% de las peticiones. El resultado es coste alto, señal
ahogada en ruido y queries lentas. La observabilidad útil es la que responde
preguntas, no la que acumula datos. Empieza por RED y USE, añade lo demás
cuando una pregunta concreta lo exija.

### Creer que el sampling de trazas pierde información crítica

Por miedo se trazan todas las peticiones. En volumen alto eso es carísimo y
casi nunca necesario. El tail-based sampling permite quedarte solo con las
trazas interesantes (errores, latencia alta) descartando las que salieron bien
y rápido. Una traza de una petición exitosa de 20ms número 4 millones aporta
poco.

### Olvidar que `rate()` necesita counters monótonos

`rate()` y `increase()` en Prometheus asumen que el counter solo sube (y
detectan reinicios a cero). Si un pipeline rompe esa propiedad (como
`prometheus receiver -> prometheusremotewrite` en el Collector, documentado en
la Fase 5), los cálculos de tasa dan valores absurdos. Por eso en este proyecto
los exporters de infraestructura los scrapea Prometheus directamente.

### Asumir que un dashboard verde significa que todo va bien

Un dashboard solo muestra lo que decidiste medir. Si no tienes un panel para
un modo de fallo, ese fallo es invisible aunque el dashboard esté todo verde.
La observabilidad (a diferencia de la monitorización) sirve precisamente para
investigar lo que no anticipaste medir.

### Tratar el p99 como "el peor caso"

El p99 significa que 1 de cada 100 peticiones es peor que ese valor. Con
millones de peticiones, ese 1% son miles de usuarios con mala experiencia.
El promedio (`avg`) miente aún más: oculta los outliers que son justo los que
importan. Mira percentiles altos (p95, p99, p999), no medias.

### Confundir `up` con salud real del servicio

`up=1` solo significa que Prometheus pudo scrapear el endpoint. No significa
que el servicio funcione correctamente, solo que responde. Un servicio puede
tener `up=1` y estar devolviendo 500 a todos los pagos. Por eso en la Fase 8
se alerta sobre `probe_success` del blackbox y sobre tasas de error, no solo
sobre `up`.

---

<a name="14-preguntas-entrevista"></a>
## 🎯 14. Preguntas de entrevista

Preguntas típicas de entrevistas SRE/Platform/DevOps sobre observabilidad, con
pistas de respuesta. Construir este proyecto te da material real para
responderlas con ejemplos concretos.

**¿Cuál es la diferencia entre monitorización y observabilidad?**
Monitorización responde preguntas conocidas de antemano (dashboards y alertas
sobre fallos previstos). Observabilidad permite investigar lo desconocido sin
desplegar código, gracias a datos ricos y correlacionados. Monitorización =
qué falla, observabilidad = por qué.

**¿Qué son los tres pilares y cómo se correlacionan?**
Métricas (cuánto/cuán rápido), logs (qué pasó en un evento), trazas (recorrido
de una petición). Se correlacionan via `trace_id`: una métrica con exemplar
lleva a una traza, la traza lleva a los logs exactos de esa petición.

**¿Por qué es peligrosa la alta cardinalidad? Da un ejemplo.**
Cada combinación de labels crea una serie temporal. Un label como `user_id`
con millones de valores genera millones de series y revienta Prometheus en
memoria/disco. Ejemplo: `http_requests_total{user_id="..."}`. Solución: esos
datos van en logs o trazas, no en labels.

**Diferencia entre pull y push. ¿Cuándo usar cada uno?**
Pull (Prometheus scrapea `/metrics`): el servidor controla el ritmo, detecta
caídas con `up`, ideal para servicios estables. Push (OTLP al Collector): la
app envía sus datos, ideal para trabajos efímeros que mueren antes de ser
scrapeados. Se pueden combinar.

**¿Qué es RED? ¿Y USE? ¿Cuándo usar cada uno?**
RED (Rate, Errors, Duration) para servicios de cara al usuario. USE
(Utilization, Saturation, Errors) para recursos de infraestructura. RED te
dice si los usuarios sufren, USE te dice qué recurso es el cuello de botella.

**Explica SLI, SLO, SLA y error budget.**
SLI es la métrica (% de peticiones bajo 300ms). SLO es el objetivo interno
(99.5% en 30 días). SLA es el contrato con penalizaciones. El error budget es
el margen de fallo que permite el SLO (0.5%): se gasta en despliegues
arriesgados; cuando se agota, se congela el cambio.

**¿Cómo funciona el context propagation en trazas distribuidas?**
El servicio origen inyecta `trace_id` y `span_id` en la cabecera HTTP
`traceparent` (W3C). El servicio destino la extrae, crea un span hijo con el
mismo `trace_id`, y lo envía al backend. Así una sola traza recorre varios
servicios. En este proyecto se implementó entre payment-api (Python) y
fraud-service (Go).

**¿Por qué separar Prometheus de Alertmanager?**
Prometheus decide cuándo una alerta existe (evalúa reglas). Alertmanager decide
qué hacer con ella (agrupar, silenciar, inhibir, enrutar). Esa separación
permite gestionar la fatiga de alertas sin tocar las reglas de detección.

**¿Cómo darías retención larga a Prometheus sin que explote en disco?**
Prometheus local guarda poco tiempo (días). Para retención larga se usa Thanos
(o Mimir/Cortex): un sidecar sube los bloques TSDB a object storage S3, y
Thanos Store los sirve para queries históricas. El almacenamiento barato (S3)
sustituye al disco local caro.

**¿Cómo evitas perder métricas en una topología de Prometheus en HA?**
Dos instancias de Prometheus scrapeando los mismos targets, cada una con un
label `replica` distinto. Thanos Query deduplica por ese label, mostrando una
sola serie aunque internamente haya dos. Si una instancia cae, la otra cubre
el hueco. Implementado en la Fase 9.

**¿Qué es un exemplar?**
Un puntero desde un punto concreto de una métrica (por ejemplo, un bucket de
histograma de latencia) a una traza específica que contribuyó a ese valor.
Es el pegamento que conecta métricas con trazas sin meter alta cardinalidad.

---

<a name="15-glosario"></a>
## 📖 15. Glosario

| Término | Definición |
|---|---|
| **Agregación** | Combinar muchos puntos de datos en uno (sum, avg, rate) para reducir volumen y ver tendencias |
| **Alertmanager** | Componente que recibe alertas de Prometheus y gestiona agrupación, silenciado, inhibición y enrutamiento |
| **Cardinalidad** | Número de series temporales únicas que genera una métrica (una por cada combinación de labels) |
| **Cardinalidad alta** | Labels con muchos valores posibles (user_id, IP); peligrosa porque multiplica las series |
| **Chunk** | Bloque comprimido de logs (Loki) o de datos de serie temporal, unidad de almacenamiento |
| **Counter** | Métrica que solo incrementa (peticiones totales); se consulta con rate()/increase() |
| **Context propagation** | Pasar el contexto de traza (trace_id, span_id) entre servicios via cabeceras como traceparent |
| **Deduplicación** | Eliminar series duplicadas que vienen de réplicas distintas de Prometheus (Thanos por label replica) |
| **Error budget** | Margen de fallo permitido por un SLO; se gasta en riesgo y, agotado, congela cambios |
| **Exemplar** | Puntero desde un punto de una métrica a una traza concreta que contribuyó a ese valor |
| **Exporter** | Proceso que expone métricas de un sistema en formato Prometheus (node_exporter, postgres_exporter) |
| **Gauge** | Métrica que sube y baja (temperatura, memoria usada, conexiones activas) |
| **Histogram** | Métrica que distribuye observaciones en buckets; permite calcular percentiles (latencia) |
| **Instrumentación** | Añadir código (o auto-instrumentación) que genera telemetría desde la aplicación |
| **Label** | Par clave-valor que dimensiona una métrica (method="GET"); su cardinalidad importa mucho |
| **OTLP** | OpenTelemetry Protocol, el formato/protocolo estándar para enviar telemetría (gRPC o HTTP) |
| **Pull** | Modelo donde el servidor va a buscar las métricas scrapeando endpoints (Prometheus) |
| **Push** | Modelo donde la app envía activamente su telemetría a un colector (OTLP) |
| **Percentil (p50, p99)** | Valor bajo el cual cae ese porcentaje de observaciones; p99=300ms significa 1% peor que 300ms |
| **RED** | Rate, Errors, Duration: las tres métricas clave de un servicio de cara al usuario |
| **Remote write** | Mecanismo de Prometheus para enviar métricas a un almacenamiento remoto |
| **Sampling** | Quedarse con una fracción de las trazas para reducir coste (head-based o tail-based) |
| **Scrape** | Acción de Prometheus de ir a buscar las métricas a un endpoint /metrics |
| **Sidecar** | Contenedor que acompaña a otro para añadirle capacidades (Thanos Sidecar junto a Prometheus) |
| **Signal** | Cada tipo de telemetría en OpenTelemetry: traces, metrics, logs, profiles |
| **SLI / SLO / SLA** | Indicador medido / objetivo interno / contrato con cliente |
| **Span** | Unidad de trabajo dentro de una traza (una operación con inicio, fin y atributos) |
| **TSDB** | Time Series Database, el motor de almacenamiento de Prometheus optimizado para series temporales |
| **Trace / Traza** | Recorrido completo de una petición a través de uno o varios servicios |
| **trace_id** | Identificador único que comparten todos los spans y logs de una misma petición |
| **traceparent** | Cabecera HTTP del estándar W3C que transporta el contexto de traza entre servicios |
| **USE** | Utilization, Saturation, Errors: las tres métricas clave de un recurso de infraestructura |
| **WAL** | Write-Ahead Log, buffer en disco que da durabilidad ante caídas antes de persistir los datos |

---

<a name="16-referencias"></a>
## 📚 16. Referencias

| Herramienta | Documentación |
|---|---|
| FastAPI | https://fastapi.tiangolo.com |
| OpenTelemetry Python | https://opentelemetry.io/docs/instrumentation/python |
| Prometheus | https://prometheus.io/docs |
| Grafana Loki | https://grafana.com/docs/loki |
| Grafana Tempo | https://grafana.com/docs/tempo |
| Thanos | https://thanos.io/tip/thanos |
| MinIO | https://min.io/docs |
