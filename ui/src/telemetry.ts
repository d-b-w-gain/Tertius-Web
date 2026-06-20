import { context, SpanKind, SpanStatusCode, trace, type Attributes, type Span } from '@opentelemetry/api'
import { ZoneContextManager } from '@opentelemetry/context-zone'
import { OTLPTraceExporter } from '@opentelemetry/exporter-trace-otlp-http'
import { registerInstrumentations } from '@opentelemetry/instrumentation'
import { DocumentLoadInstrumentation } from '@opentelemetry/instrumentation-document-load'
import { FetchInstrumentation } from '@opentelemetry/instrumentation-fetch'
import { resourceFromAttributes } from '@opentelemetry/resources'
import { BatchSpanProcessor } from '@opentelemetry/sdk-trace-base'
import { WebTracerProvider } from '@opentelemetry/sdk-trace-web'
import {
  ATTR_DEPLOYMENT_ENVIRONMENT_NAME,
  ATTR_SERVICE_NAME,
  ATTR_SERVICE_VERSION,
} from '@opentelemetry/semantic-conventions'

type BrowserEnv = ImportMetaEnv & {
  MODE?: string
  VITE_OTEL_ENABLED?: string
  VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT?: string
  VITE_OTEL_SERVICE_NAME?: string
}

export interface BrowserTelemetryConfig {
  enabled: boolean
  endpoint: string
  serviceName: string
  environment: string
}

let telemetryInitialized = false
let errorHandlersInstalled = false

function envFlagEnabled(value: string | undefined) {
  return ['1', 'true', 'yes', 'on'].includes((value ?? '').trim().toLowerCase())
}

export function readBrowserTelemetryConfig(env: BrowserEnv = import.meta.env): BrowserTelemetryConfig {
  const environment = env.MODE || 'production'

  return {
    enabled: environment !== 'test' && envFlagEnabled(env.VITE_OTEL_ENABLED),
    endpoint: env.VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT?.trim() || '/otel/v1/traces',
    serviceName: env.VITE_OTEL_SERVICE_NAME?.trim() || 'tertius-ui',
    environment,
  }
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
}

export function sameOriginApiUrlPattern(origin = window.location.origin) {
  return new RegExp(`^${escapeRegExp(origin)}/api(?:[/?#]|$)`)
}

export function ignoredFetchUrlPattern(origin = window.location.origin) {
  return new RegExp(`^(?!${escapeRegExp(origin)}/api(?:[/?#]|$)).*`)
}

export function isSameOriginApiUrl(url: string, origin = window.location.origin) {
  return sameOriginApiUrlPattern(origin).test(new URL(url, origin).href)
}

function errorType(error: unknown) {
  if (error && typeof error === 'object' && 'name' in error && typeof error.name === 'string') {
    return error.name
  }
  return typeof error
}

function markSpanError(span: Span, error: unknown, source?: string) {
  span.setStatus({ code: SpanStatusCode.ERROR })
  span.addEvent('exception', {
    'exception.type': errorType(error),
    ...(source ? { 'error.source': source } : {}),
  })
}

function isPromiseLike(value: unknown): value is Promise<unknown> {
  return Boolean(value && typeof (value as { then?: unknown }).then === 'function')
}

export function recordFrontendError(error: unknown, source = 'manual') {
  const span = trace.getTracer('tertius-ui').startSpan('ui.frontend_error', {
    kind: SpanKind.INTERNAL,
    attributes: {
      'error.source': source,
    },
  })

  markSpanError(span, error, source)
  span.end()
}

function installErrorHandlers() {
  if (errorHandlersInstalled || typeof window === 'undefined') {
    return
  }

  window.addEventListener('error', (event) => {
    recordFrontendError(event.error, 'window.error')
  })
  window.addEventListener('unhandledrejection', (event) => {
    recordFrontendError(event.reason, 'window.unhandledrejection')
  })
  errorHandlersInstalled = true
}

export function startInteractionSpan(name: string, attributes: Attributes = {}) {
  return trace.getTracer('tertius-ui').startSpan(`ui.${name}`, {
    kind: SpanKind.INTERNAL,
    attributes: {
      'app.interaction.name': name,
      ...attributes,
    },
  })
}

export function runWithInteractionSpan<T>(name: string, action: () => T): T
export function runWithInteractionSpan<T>(
  name: string,
  attributes: Attributes,
  action: () => T,
): T
export function runWithInteractionSpan<T>(
  name: string,
  attributesOrAction: Attributes | (() => T),
  maybeAction?: () => T,
): T {
  const attributes = typeof attributesOrAction === 'function' ? {} : attributesOrAction
  const action = typeof attributesOrAction === 'function' ? attributesOrAction : maybeAction

  if (!action) {
    throw new Error('runWithInteractionSpan requires an action')
  }

  const span = startInteractionSpan(name, attributes)

  return context.with(trace.setSpan(context.active(), span), () => {
    try {
      const result = action()
      if (isPromiseLike(result)) {
        return result.then(
          (value) => {
            span.end()
            return value
          },
          (error: unknown) => {
            markSpanError(span, error)
            span.end()
            throw error
          },
        ) as T
      }

      span.end()
      return result
    } catch (error) {
      markSpanError(span, error)
      span.end()
      throw error
    }
  })
}

export function initializeTelemetry(config = readBrowserTelemetryConfig()) {
  if (!config.enabled || telemetryInitialized) {
    return false
  }

  const provider = new WebTracerProvider({
    resource: resourceFromAttributes({
      [ATTR_SERVICE_NAME]: config.serviceName,
      [ATTR_SERVICE_VERSION]: __GIT_COMMIT__,
      [ATTR_DEPLOYMENT_ENVIRONMENT_NAME]: config.environment,
      'deployment.environment': config.environment,
    }),
    spanProcessors: [
      new BatchSpanProcessor(new OTLPTraceExporter({
        url: config.endpoint,
      })),
    ],
  })

  provider.register({
    contextManager: new ZoneContextManager(),
  })

  registerInstrumentations({
    instrumentations: [
      new DocumentLoadInstrumentation(),
      new FetchInstrumentation({
        clearTimingResources: true,
        ignoreUrls: [ignoredFetchUrlPattern()],
        propagateTraceHeaderCorsUrls: [sameOriginApiUrlPattern()],
      }),
    ],
  })

  installErrorHandlers()
  telemetryInitialized = true
  return true
}
