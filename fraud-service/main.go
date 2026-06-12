package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"net/http"
	"os"
	"strings"
	"time"

	"go.opentelemetry.io/otel"
	"go.opentelemetry.io/otel/attribute"
	"go.opentelemetry.io/otel/exporters/otlp/otlptrace/otlptracegrpc"
	"go.opentelemetry.io/otel/propagation"
	"go.opentelemetry.io/otel/sdk/resource"
	sdktrace "go.opentelemetry.io/otel/sdk/trace"
	semconv "go.opentelemetry.io/otel/semconv/v1.26.0"
	"go.opentelemetry.io/otel/trace"
	"google.golang.org/grpc"
	"google.golang.org/grpc/credentials/insecure"
)

type CheckRequest struct {
	Amount    float64 `json:"amount"`
	Currency  string  `json:"currency"`
	PaymentID string  `json:"payment_id"`
}

type CheckResponse struct {
	Status string `json:"status"`
	Reason string `json:"reason,omitempty"`
}

func initTracer(ctx context.Context) (*sdktrace.TracerProvider, error) {
	endpoint := os.Getenv("OTEL_EXPORTER_OTLP_ENDPOINT")
	if endpoint == "" {
		endpoint = "otel-collector:4317"
	}

	conn, err := grpc.NewClient(endpoint,
		grpc.WithTransportCredentials(insecure.NewCredentials()),
	)
	if err != nil {
		return nil, fmt.Errorf("error creando conexión gRPC: %w", err)
	}

	exporter, err := otlptracegrpc.New(ctx, otlptracegrpc.WithGRPCConn(conn))
	if err != nil {
		return nil, fmt.Errorf("error creando exporter OTLP: %w", err)
	}

	res, err := resource.New(ctx,
		resource.WithAttributes(
			semconv.ServiceName("fraud-service"),
		),
	)
	if err != nil {
		return nil, fmt.Errorf("error creando resource: %w", err)
	}

	tp := sdktrace.NewTracerProvider(
		sdktrace.WithBatcher(exporter),
		sdktrace.WithResource(res),
	)

	otel.SetTracerProvider(tp)
	otel.SetTextMapPropagator(propagation.NewCompositeTextMapPropagator(
		propagation.TraceContext{},
		propagation.Baggage{},
	))

	return tp, nil
}

// extractRemoteContext parsea el header traceparent manualmente y lo inyecta
// en el context como remote span context.
func extractRemoteContext(ctx context.Context, r *http.Request) context.Context {
	tp := r.Header.Get("Traceparent")
	if tp == "" {
		return ctx
	}

	parts := strings.Split(tp, "-")
	if len(parts) != 4 || parts[0] != "00" {
		log.Printf("traceparent inválido: %q", tp)
		return ctx
	}

	traceID, err := trace.TraceIDFromHex(parts[1])
	if err != nil {
		log.Printf("trace_id inválido: %v", err)
		return ctx
	}

	spanID, err := trace.SpanIDFromHex(parts[2])
	if err != nil {
		log.Printf("span_id inválido: %v", err)
		return ctx
	}

	flags := trace.TraceFlags(0)
	if parts[3] == "01" || parts[3] == "03" {
		flags = trace.FlagsSampled
	}

	sc := trace.NewSpanContext(trace.SpanContextConfig{
		TraceID:    traceID,
		SpanID:     spanID,
		TraceFlags: flags,
		Remote:     true,
	})

	log.Printf("remote span context: trace_id=%s valid=%v", sc.TraceID().String(), sc.IsValid())
	return trace.ContextWithRemoteSpanContext(ctx, sc)
}

func checkHandler(w http.ResponseWriter, r *http.Request) {
	ctx := extractRemoteContext(r.Context(), r)

	tracer := otel.Tracer("fraud-service")
	ctx, span := tracer.Start(ctx, "POST /check")
	defer span.End()

	log.Printf("span creado: trace_id=%s", span.SpanContext().TraceID().String())

	var req CheckRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		http.Error(w, "request inválido", http.StatusBadRequest)
		return
	}

	_, checkSpan := tracer.Start(ctx, "fraud.check")
	defer checkSpan.End()

	checkSpan.SetAttributes(
		attribute.Float64("payment.amount", req.Amount),
		attribute.String("payment.currency", req.Currency),
		attribute.String("payment.id", req.PaymentID),
	)

	resp := CheckResponse{Status: "approved"}

	if req.Amount > 1000 {
		resp.Status = "rejected"
		resp.Reason = fmt.Sprintf("importe %.2f %s supera el límite de 1000 %s",
			req.Amount, req.Currency, req.Currency)
		checkSpan.SetAttributes(attribute.String("fraud.result", "rejected"))
	} else {
		checkSpan.SetAttributes(attribute.String("fraud.result", "approved"))
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(resp)
}

func main() {
	ctx := context.Background()

	tp, err := initTracer(ctx)
	if err != nil {
		log.Fatalf("error inicializando tracer: %v", err)
	}
	defer func() {
		ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
		defer cancel()
		if err := tp.Shutdown(ctx); err != nil {
			log.Printf("error apagando tracer: %v", err)
		}
	}()

	mux := http.NewServeMux()
	mux.HandleFunc("/check", checkHandler)
	mux.HandleFunc("/health", func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "application/json")
		fmt.Fprint(w, `{"status":"ok"}`)
	})

	port := os.Getenv("PORT")
	if port == "" {
		port = "8001"
	}

	log.Printf("fraud-service escuchando en :%s", port)
	if err := http.ListenAndServe(":"+port, mux); err != nil {
		log.Fatalf("error del servidor: %v", err)
	}
}
