import type { FormEventHandler, ReactNode } from 'react'
import type { LlmModelOption } from '../../shared/projectStorage'
import type { ChatMessage } from '../model/conversation'
import { ProgressActivity } from './ProgressActivity'

type ConversationPanelProps = {
  statusText: string
  compileFormat: string
  compileQuality: string
  projectSelector: ReactNode
  messages: ChatMessage[]
  selectedMessageId: string | null
  llmModels: LlmModelOption[]
  selectedModelId: string
  prompt: string
  error: string | null
  isSubmitting: boolean
  canSubmit: boolean
  onClose: () => void
  onRefresh: () => void
  onSelectModel: (modelId: string) => void
  onSelectMessage: (messageId: string) => void
  onPromptChange: (prompt: string) => void
  onSubmit: FormEventHandler<HTMLFormElement>
  onMessageRef?: (node: HTMLDivElement | null, messageId: string) => void
}

export function ConversationPanel({
  statusText,
  compileFormat,
  compileQuality,
  projectSelector,
  messages,
  selectedMessageId,
  llmModels,
  selectedModelId,
  prompt,
  error,
  isSubmitting,
  canSubmit,
  onClose,
  onRefresh,
  onSelectModel,
  onSelectMessage,
  onPromptChange,
  onSubmit,
  onMessageRef,
}: ConversationPanelProps) {
  return (
    <aside
      role="complementary"
      aria-label="Generate Design conversation"
      className="pointer-events-auto absolute inset-x-3 bottom-3 top-16 z-20 flex min-h-0 flex-col rounded border border-slate-700 bg-slate-950/95 shadow-2xl shadow-slate-950/60 backdrop-blur md:left-auto md:right-4 md:w-[28rem]"
    >
      <div className="border-b border-slate-800 p-4">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="text-base font-semibold text-slate-100">Generate Design</h2>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <button
              type="button"
              onClick={onRefresh}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              Refresh
            </button>
            <button
              type="button"
              aria-expanded="true"
              onClick={onClose}
              className="rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-300 hover:bg-slate-700"
            >
              Close Generate Design conversation
            </button>
          </div>
        </div>
        <div className="mt-4">
          {projectSelector}
        </div>
      </div>

      <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
        <span className="min-w-0 text-sm text-slate-300">{statusText}</span>
        <span className="shrink-0 rounded border border-slate-800 bg-slate-900 px-2 py-1 font-mono text-[10px] text-slate-500">
          {compileFormat}/{compileQuality}
        </span>
      </div>

      <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3 text-xs">
        <label htmlFor="generate-design-model" className="font-semibold text-slate-200">
          AI model
        </label>
        <select
          id="generate-design-model"
          aria-label="AI model"
          value={selectedModelId}
          onChange={event => onSelectModel(event.currentTarget.value)}
          disabled={!llmModels.some(model => model.enabled)}
          className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-xs text-slate-200 outline-none focus:border-cyan-500 disabled:cursor-not-allowed disabled:text-slate-500"
        >
          {llmModels.map(model => (
            <option key={model.id} value={model.id} disabled={!model.enabled}>
              {model.label}
            </option>
          ))}
        </select>
      </div>

      <div className="min-h-0 flex-1 overflow-auto p-4">
        {messages.length === 0 ? (
          <div className="rounded border border-slate-800 bg-slate-900/40 p-4 text-sm text-slate-500">
            Generated design messages will appear here.
          </div>
        ) : (
          <div className="space-y-3">
            {messages.map(message => {
              const hasActivity = message.role === 'assistant' && Boolean(
                message.progressDisclosure
                || message.progressActive
                || message.progress?.events.length
              )
              return (
                <div
                  ref={message.role === 'assistant'
                    ? node => onMessageRef?.(node, message.id)
                    : undefined}
                  key={message.renderKey || message.id}
                  className={`overflow-hidden rounded border transition-colors ${
                    selectedMessageId === message.id
                      ? 'border-cyan-700 bg-cyan-950/30'
                      : 'border-slate-800 bg-slate-900/50'
                  }`}
                >
                  <button
                    type="button"
                    onClick={() => onSelectMessage(message.id)}
                    className={`block w-full p-3 text-left transition-colors ${
                      selectedMessageId === message.id ? '' : 'hover:bg-slate-900'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <span className={message.role === 'assistant' ? 'text-xs font-semibold text-cyan-300' : 'text-xs font-semibold text-slate-300'}>
                        {message.role === 'assistant' ? 'Assistant' : 'Prompt'}
                      </span>
                      {message.compileStatus && (
                        <span className="rounded bg-slate-800 px-2 py-0.5 text-[10px] text-slate-400">{message.compileStatus}</span>
                      )}
                    </div>
                    <p className="mt-2 whitespace-pre-wrap text-xs leading-5 text-slate-300">{message.content}</p>
                    {(message.model || message.usage) && (
                      <div className="mt-2 font-mono text-[10px] text-slate-500">
                        {[message.model, message.usage ? `${message.usage.total_tokens} tokens` : ''].filter(Boolean).join(' / ')}
                      </div>
                    )}
                  </button>
                  {hasActivity && (
                    <ProgressActivity
                      progress={message.progress}
                      active={Boolean(message.progressActive)}
                      defaultOpen={Boolean(message.progressDisclosure || message.progressActive)}
                    />
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      <form onSubmit={onSubmit} className="border-t border-slate-800 p-4">
        <textarea
          value={prompt}
          onChange={event => onPromptChange(event.currentTarget.value)}
          placeholder="Describe the CAD design or modification..."
          className="h-28 w-full resize-none rounded border border-slate-700 bg-slate-950 p-3 text-sm text-slate-100 outline-none placeholder:text-slate-600 focus:border-cyan-500"
        />
        {error && <div className="rounded border border-red-900/60 bg-red-950/40 px-3 py-2 text-xs text-red-200">{error}</div>}
        <button
          type="submit"
          disabled={!canSubmit}
          className="mt-3 w-full rounded bg-cyan-600 px-4 py-3 text-base font-semibold text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {isSubmitting ? 'Generating...' : 'Generate Design'}
        </button>
      </form>
    </aside>
  )
}
