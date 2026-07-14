import { useEffect, useMemo, useState } from 'react'
import { apiFetch } from '../../api/client'
import { useAuth } from '../../auth/AuthProvider'

export const AI_USAGE_EVENT = 'tertius:ai-usage'

type AiUsageGaugeProps = {
  serverUrl: string
}

type LlmUsageSummary = {
  tenant_daily_token_quota?: number
  tenant_tokens_used_today?: number
  tenant_tokens_remaining_today?: number
  user_daily_token_quota?: number
  user_tokens_used_today?: number
  user_tokens_remaining_today?: number
  last_edit?: {
    total_tokens?: number
    model?: string
  } | null
}

function readLocalUsage(): number {
  const raw = localStorage.getItem('tertius:ai-tokens-used-today')
  const parsed = raw ? Number(raw) : 0
  return Number.isFinite(parsed) ? Math.max(0, parsed) : 0
}

function formatTokens(value: number) {
  if (value >= 1_000_000) return `${(value / 1_000_000).toFixed(1)}M`
  if (value >= 1_000) return `${(value / 1_000).toFixed(1)}k`
  return String(value)
}

export function recordAiUsage(tokens: number) {
  if (!Number.isFinite(tokens) || tokens <= 0) return
  const next = readLocalUsage() + tokens
  localStorage.setItem('tertius:ai-tokens-used-today', String(next))
  window.dispatchEvent(new CustomEvent(AI_USAGE_EVENT, { detail: { tokens, total: next } }))
}

export function AiUsageGauge({ serverUrl }: AiUsageGaugeProps) {
  const { authMode, getAccessToken } = useAuth()
  const [summary, setSummary] = useState<LlmUsageSummary | null>(null)
  const [localUsage, setLocalUsage] = useState(readLocalUsage)

  useEffect(() => {
    const handleUsage = (event: Event) => {
      const detail = (event as CustomEvent<{ total?: number }>).detail
      setLocalUsage(Number.isFinite(detail?.total) ? Number(detail.total) : readLocalUsage())
    }
    window.addEventListener(AI_USAGE_EVENT, handleUsage)
    window.addEventListener('storage', handleUsage)
    return () => {
      window.removeEventListener(AI_USAGE_EVENT, handleUsage)
      window.removeEventListener('storage', handleUsage)
    }
  }, [])

  useEffect(() => {
    if (authMode === 'guest') return
    let cancelled = false

    const loadSummary = async () => {
      try {
        const response = await apiFetch(`${serverUrl}/llm-usage/today`, getAccessToken)
        if (!response.ok) return
        const data = await response.json()
        if (!cancelled) setSummary(data)
      } catch {
        if (!cancelled) setSummary(null)
      }
    }

    void loadSummary()
    const interval = window.setInterval(loadSummary, 60_000)
    const handleUsage = () => void loadSummary()
    window.addEventListener(AI_USAGE_EVENT, handleUsage)
    return () => {
      cancelled = true
      window.clearInterval(interval)
      window.removeEventListener(AI_USAGE_EVENT, handleUsage)
    }
  }, [authMode, getAccessToken, serverUrl])

  const used = summary?.tenant_tokens_used_today ?? localUsage
  const quota = summary?.tenant_daily_token_quota ?? 0
  const percent = useMemo(() => {
    if (!quota) return 0
    return Math.min(100, Math.max(0, (used / quota) * 100))
  }, [quota, used])
  const remaining = summary?.tenant_tokens_remaining_today
  const title = quota
    ? `${formatTokens(remaining ?? Math.max(0, quota - used))} tokens remaining today`
    : 'AI token usage for this browser session'

  return (
    <div
      className="fixed bottom-4 left-4 z-40 w-56 rounded border border-slate-700 bg-slate-900/95 px-3 py-2 text-xs text-slate-300 shadow-xl backdrop-blur"
      title={title}
      aria-label="AI usage gauge"
    >
      <div className="flex items-center justify-between gap-3">
        <span className="font-semibold text-slate-100">Tenant AI Usage</span>
        <span className="font-mono text-cyan-300">
          {quota
            ? `${formatTokens(used)} / ${formatTokens(quota)}`
            : `${formatTokens(used)} used`}
        </span>
      </div>
      <div className="mt-2 h-2 overflow-hidden rounded bg-slate-800">
        <div
          className="h-full rounded bg-cyan-500 transition-all"
          style={{ width: quota ? `${percent}%` : used > 0 ? '18%' : '0%' }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[10px] text-slate-500">
        <span>{authMode === 'guest' ? 'Login required' : summary ? 'Today' : 'Session'}</span>
        <span>{summary?.last_edit?.model || 'LLM edits'}</span>
      </div>
    </div>
  )
}
