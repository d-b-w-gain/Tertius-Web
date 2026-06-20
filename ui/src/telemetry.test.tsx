import { describe, expect, it } from 'vitest'
import {
  ignoredFetchUrlPattern,
  isSameOriginApiUrl,
  readBrowserTelemetryConfig,
  sameOriginApiUrlPattern,
} from './telemetry'

describe('browser telemetry configuration', () => {
  it('stays disabled by default and in test mode', () => {
    expect(readBrowserTelemetryConfig({ MODE: 'development' } as unknown as ImportMetaEnv).enabled).toBe(false)
    expect(readBrowserTelemetryConfig({
      MODE: 'test',
      VITE_OTEL_ENABLED: 'true',
    } as unknown as ImportMetaEnv).enabled).toBe(false)
  })

  it('uses explicit Vite env to enable browser export settings', () => {
    expect(readBrowserTelemetryConfig({
      MODE: 'production',
      VITE_OTEL_ENABLED: 'true',
      VITE_OTEL_EXPORTER_OTLP_TRACES_ENDPOINT: '/custom/v1/traces',
      VITE_OTEL_SERVICE_NAME: 'custom-ui',
    } as unknown as ImportMetaEnv)).toEqual({
      enabled: true,
      endpoint: '/custom/v1/traces',
      serviceName: 'custom-ui',
      environment: 'production',
    })
  })
})

describe('browser telemetry URL filtering', () => {
  const origin = 'https://tertius.example'

  it('matches same-origin API URLs only', () => {
    const apiPattern = sameOriginApiUrlPattern(origin)

    expect(apiPattern.test('https://tertius.example/api')).toBe(true)
    expect(apiPattern.test('https://tertius.example/api/intus/project_name')).toBe(true)
    expect(apiPattern.test('https://tertius.example/api?health=true')).toBe(true)
    expect(apiPattern.test('https://tertius.example/assets/index.js')).toBe(false)
    expect(apiPattern.test('https://api.tertius.example/api/intus/project_name')).toBe(false)
  })

  it('ignores fetch spans for non-API and cross-origin URLs', () => {
    const ignorePattern = ignoredFetchUrlPattern(origin)

    expect(ignorePattern.test('https://tertius.example/assets/index.js')).toBe(true)
    expect(ignorePattern.test('https://tertius.example/otel/v1/traces')).toBe(true)
    expect(ignorePattern.test('https://api.tertius.example/api/intus/project_name')).toBe(true)
    expect(ignorePattern.test('https://tertius.example/api/intus/project_name')).toBe(false)
  })

  it('normalizes relative URLs before checking API origin', () => {
    expect(isSameOriginApiUrl('/api/intus/project_name', origin)).toBe(true)
    expect(isSameOriginApiUrl('/realms/tertius', origin)).toBe(false)
    expect(isSameOriginApiUrl('https://tertius.example/api/intus/project_name', origin)).toBe(true)
    expect(isSameOriginApiUrl('https://api.tertius.example/api/intus/project_name', origin)).toBe(false)
  })
})
