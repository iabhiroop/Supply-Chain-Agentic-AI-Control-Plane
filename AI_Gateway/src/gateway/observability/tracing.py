"""
OpenTelemetry Tracing Setup.
"""

import logging
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.semconv.resource import ResourceAttributes

logger = logging.getLogger(__name__)

class TracingManager:
    """Manages OpenTelemetry tracing lifecycle."""
    
    def __init__(self, service_name: str = "ai-gateway", otlp_endpoint: str | None = None):
        self.service_name = service_name
        self.otlp_endpoint = otlp_endpoint
        self._setup_tracing()
        
    def _setup_tracing(self):
        resource = Resource(attributes={
            ResourceAttributes.SERVICE_NAME: self.service_name
        })
        
        provider = TracerProvider(resource=resource)
        
        # Always add console exporter for dev, but in prod we'd configure this differently
        if self.otlp_endpoint:
            # Export to Jaeger/Zipkin/Datadog etc via OTLP
            otlp_exporter = OTLPSpanExporter(endpoint=self.otlp_endpoint)
            provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
            logger.info(f"Initialized OTEL Tracing with OTLP endpoint: {self.otlp_endpoint}")
        else:
            # Fallback to console
            processor = BatchSpanProcessor(ConsoleSpanExporter())
            provider.add_span_processor(processor)
            logger.info("Initialized OTEL Tracing with Console Exporter")
            
        trace.set_tracer_provider(provider)
        self.tracer = trace.get_tracer(__name__)
        
    def get_tracer(self):
        return self.tracer
