import { useState } from 'react'
import type {
  LlmEditProgressEvent,
  LlmEditProgressSnapshot,
  PiAgentToolName,
} from '../../shared/projectStorage'

const TOOL_ACTIVITY_LABELS: Record<PiAgentToolName, string> = {
  read: 'Read',
  edit: 'Edit',
  write: 'Write',
  grep: 'Search',
  find: 'Find',
  ls: 'List',
}

function toolActivityLabel(event: LlmEditProgressEvent) {
  if (!event.tool_name) return ''
  const action = TOOL_ACTIVITY_LABELS[event.tool_name]
  if (event.kind === 'tool_started') return `${action} started`
  return `${action} ${event.is_error ? 'failed' : 'completed'}`
}

type ProgressActivityProps = {
  progress?: LlmEditProgressSnapshot
  active?: boolean
  defaultOpen?: boolean
}

export function ProgressActivity({
  progress,
  active = false,
  defaultOpen = false,
}: ProgressActivityProps) {
  // React has no native <details> defaultOpen prop. This mount-stable value
  // initializes `open`, then leaves the disclosure browser-controlled so later
  // progress renders do not overwrite the user's toggle.
  const [initiallyOpen] = useState(defaultOpen)
  const eventCount = progress?.events.length || 0
  const progressState = active
    ? eventCount > 0 ? 'Working' : 'Starting'
    : 'Complete'
  const latestEvent = progress?.events.at(-1)
  const latestLabel = latestEvent?.kind === 'reasoning_delta'
    ? 'Reasoning updated'
    : latestEvent ? toolActivityLabel(latestEvent) : ''
  return (
    <>
      {active && progress && latestLabel && (
        <p role="status" aria-live="polite" aria-atomic="true" className="sr-only">
          AI activity updated: {progress.events.length} {progress.events.length === 1 ? 'event' : 'events'}. Latest: {latestLabel}. Sequence: {progress.last_sequence}.
        </p>
      )}
      <details open={initiallyOpen} className="border-t border-slate-800 bg-slate-950/35 px-3 py-2">
        <summary className="flex cursor-pointer select-none items-center gap-2 text-[10px] uppercase tracking-[0.12em] text-slate-400">
          <span className="font-sans font-semibold text-slate-300">Thinking &amp; activity</span>
          <span className={`ml-auto font-mono font-semibold ${active ? 'text-cyan-300' : 'text-slate-400'}`}>
            {progressState}
          </span>
          <span className="font-mono text-slate-400">
            {eventCount} {eventCount === 1 ? 'update' : 'updates'}
          </span>
        </summary>
        {progress && progress.events.length > 0 ? (
          <ol className="ml-1 mt-3 space-y-3 border-l border-slate-700/80 pl-4">
            {progress.truncated_before_sequence !== null && (
              <li className="relative text-[11px] leading-4 text-slate-300">
                <span className="absolute -left-[1.19rem] top-1.5 h-1.5 w-1.5 rounded-full bg-slate-700" />
                Earlier activity was truncated.
              </li>
            )}
            {progress.events.map(event => (
              <li key={event.sequence} className="relative text-[11px] leading-4 text-slate-400">
                <span className={`absolute -left-[1.19rem] top-1.5 h-1.5 w-1.5 rounded-full ${
                  event.kind === 'tool_finished' && event.is_error ? 'bg-red-500' : 'bg-cyan-700'
                }`} />
                {event.kind === 'reasoning_delta' ? (
                  <p className="whitespace-pre-wrap break-words text-slate-400">{event.text?.slice(0, 1000)}</p>
                ) : (
                  <div className="flex min-w-0 items-baseline gap-2">
                    <span className={event.is_error ? 'font-medium text-red-300' : 'font-medium text-slate-300'}>
                      {toolActivityLabel(event)}
                    </span>
                    {event.target && (
                      <code className="min-w-0 break-all font-mono text-[10px] text-slate-300">
                        {event.target}
                      </code>
                    )}
                  </div>
                )}
              </li>
            ))}
          </ol>
        ) : (
          <p className="mt-3 text-[11px] leading-4 text-slate-400">
            {active ? 'Waiting for the first progress update…' : 'No activity details were received.'}
          </p>
        )}
      </details>
    </>
  )
}
