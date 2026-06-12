from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import create_engine, Column, String, Numeric, DateTime
from sqlalchemy.orm import DeclarativeBase, Session
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
from opentelemetry._logs import set_logger_provider
from opentelemetry.metrics import set_meter_provider
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
from opentelemetry.propagate import inject

import uuid
import os
import structlog
import logging
import httpx

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(message)s",
    level=logging.INFO,
)

def add_trace_context(logger, method, event_dict):
    span = trace.get_current_span()
    if span and span.is_recording():
        ctx = span.get_span_context()
        event_dict["trace_id"] = format(ctx.trace_id, "032x")
        event_dict["span_id"] = format(ctx.span_id, "016x")
    return event_dict

structlog.configure(
    processors=[
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        add_trace_context,
        structlog.processors.JSONRenderer(),
    ],
    logger_factory=structlog.stdlib.LoggerFactory(),
    wrapper_class=structlog.make_filtering_bound_logger(logging.INFO),
)

logger = structlog.get_logger()


# ---------------------------------------------------------------------------
# Tracing, Logging y Metrics
# ---------------------------------------------------------------------------
def setup_tracing():
    if os.getenv("OTEL_SDK_DISABLED", "false").lower() == "true":
        return trace.get_tracer(__name__), MeterProvider().get_meter(__name__)
    resource = Resource.create({SERVICE_NAME: "payment-api"})

    # Trazas
    trace_exporter = OTLPSpanExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        insecure=True,
    )
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(trace_exporter))
    trace.set_tracer_provider(provider)

    # Logs
    log_exporter = OTLPLogExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        insecure=True,
    )
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
    set_logger_provider(log_provider)
    handler = LoggingHandler(level=logging.INFO, logger_provider=log_provider)
    logging.getLogger().addHandler(handler)

    # Metricas
    metric_exporter = OTLPMetricExporter(
        endpoint=os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://otel-collector:4317"),
        insecure=True,
    )
    reader = PeriodicExportingMetricReader(
        metric_exporter,
        export_interval_millis=15000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
    set_meter_provider(meter_provider)
    meter = meter_provider.get_meter(__name__)

    return trace.get_tracer(__name__), meter

tracer, meter = setup_tracing()


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "").replace(
    "postgresql://", "postgresql+psycopg://"
)
engine = create_engine(DATABASE_URL, pool_pre_ping=True)


class Base(DeclarativeBase):
    pass


class Payment(Base):
    __tablename__ = "payments"

    id = Column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    amount = Column(Numeric(10, 2), nullable=False)
    currency = Column(String(3), default="EUR")
    status = Column(String(20), default="pending")
    created_at = Column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------
class PaymentRequest(BaseModel):
    amount: float = Field(..., gt=0, description="Importe mayor que cero")
    currency: str = Field(default="EUR", pattern="^[A-Z]{3}$")


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    amount: float
    currency: str
    status: str
    created_at: datetime


class PaymentsListResponse(BaseModel):
    payments: list[PaymentResponse]
    total: int


# ---------------------------------------------------------------------------
# Custom metrics
# ---------------------------------------------------------------------------
payments_created_total = meter.create_counter(
    "payments_created_total",
    description="Total de pagos creados exitosamente",
)

payments_amount_euros = meter.create_histogram(
    "payments_amount_euros",
    description="Distribucion de importes de pago",
    unit="EUR",
)


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(engine)
    yield


app = FastAPI(title="Payment API", version="1.0.0", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)
SQLAlchemyInstrumentor().instrument(engine=engine)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health():
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}


@app.get("/payments", response_model=PaymentsListResponse)
def get_payments():
    with Session(engine) as session:
        payments = session.query(Payment).all()
        total = len(payments)
        logger.info("payments_listed", total=total)
        return {
            "payments": [PaymentResponse.model_validate(p) for p in payments],
            "total": total,
        }


@app.post("/payments", response_model=PaymentResponse, status_code=201)
def create_payment(payment: PaymentRequest):
    with tracer.start_as_current_span("payment.process") as span:
        span.set_attribute("payment.amount", payment.amount)
        span.set_attribute("payment.currency", payment.currency)

        # Llama al fraud-service propagando el contexto de traza
        headers = {}
        inject(headers)
        fraud_url = os.getenv("FRAUD_SERVICE_URL", "http://fraud-service:8001")
        try:
            fraud_resp = httpx.post(
                f"{fraud_url}/check",
                json={
                    "amount": payment.amount,
                    "currency": payment.currency,
                    "payment_id": "",
                },
                headers=headers,
                timeout=5.0,
            )
            fraud_result = fraud_resp.json()
        except httpx.ConnectError:
            logger.warning("fraud_service_unavailable", fraud_url=fraud_url)
            fraud_result = {"status": "approved"}

        if fraud_result.get("status") == "rejected":
            span.set_attribute("fraud.result", "rejected")
            span.set_attribute("fraud.reason", fraud_result.get("reason", ""))
            from fastapi import HTTPException
            raise HTTPException(
                status_code=422,
                detail=f"Pago rechazado: {fraud_result.get('reason', 'fraude detectado')}",
            )

        span.set_attribute("fraud.result", "approved")

        with Session(engine) as session:
            new_payment = Payment(
                amount=payment.amount,
                currency=payment.currency,
            )
            session.add(new_payment)
            session.commit()
            session.refresh(new_payment)
            payments_created_total.add(1, {"currency": payment.currency})
            payments_amount_euros.record(float(new_payment.amount), {"currency": payment.currency})
            logger.info(
                "payment_created",
                payment_id=new_payment.id,
                amount=float(new_payment.amount),
                currency=new_payment.currency,
            )
            span.set_attribute("payment.id", new_payment.id)
            return PaymentResponse.model_validate(new_payment)
        