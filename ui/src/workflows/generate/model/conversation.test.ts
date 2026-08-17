import { describe, expect, it } from 'vitest'
import type { LlmEditProgressSnapshot, ProjectFileMetadata } from '../../shared/projectStorage'
import {
  buildCompileRepairPrompt,
  isNonTerminalStatus,
  mergeProgressSnapshot,
  orderEditableFiles,
} from './conversation'

function progressSnapshot(
  overrides: Partial<LlmEditProgressSnapshot> = {},
): LlmEditProgressSnapshot {
  return {
    schema_version: 1,
    execution_id: 'execution-a',
    execution_started_at: '2026-08-16T01:00:00Z',
    last_batch_sequence: 1,
    last_sequence: 1,
    truncated_before_sequence: null,
    events: [{
      sequence: 1,
      kind: 'reasoning_delta',
      text: 'Inspecting the model.',
      tool_name: null,
      target: null,
      is_error: null,
      occurred_at: '2026-08-16T01:00:01Z',
    }],
    ...overrides,
  }
}

describe('Generate conversation model', () => {
  it('orders an editable design.py first and excludes incomplete file pointers', () => {
    const metadata: ProjectFileMetadata[] = [
      { id: 'helpers-id', filename: 'helpers.py', updated_at: '2026-08-16T01:00:00Z' },
      { id: 'notes-id', filename: 'notes.py' },
      { id: 'design-id', filename: 'design.py', updated_at: '2026-08-16T02:00:00Z' },
      { id: 'stale-id', filename: 'stale.py' },
    ]

    expect(orderEditableFiles(metadata).map(file => file.filename)).toEqual([
      'design.py',
      'helpers.py',
    ])
  })

  it('classifies only queued and running statuses as non-terminal', () => {
    expect(isNonTerminalStatus('queued')).toBe(true)
    expect(isNonTerminalStatus('running')).toBe(true)
    expect(isNonTerminalStatus('succeeded')).toBe(false)
    expect(isNonTerminalStatus('failed')).toBe(false)
    expect(isNonTerminalStatus()).toBe(false)
  })

  it('constructs the compile repair prompt with the existing exact wording', () => {
    expect(buildCompileRepairPrompt('Make a 2 mm plate.', {
      error_code: 'sandbox_error',
      user_message: 'The design could not compile.',
      error: 'AttributeError: missing API',
    })).toBe([
      'The previous generated design failed to compile in the Tertius build123d sandbox.',
      'Fix the Python source so it compiles successfully. Preserve the original design intent.',
      'Do not use APIs shown as missing in the traceback. Return the full corrected file content.',
      '',
      'Original user request:\nMake a 2 mm plate.',
      '',
      'Error code: sandbox_error',
      '',
      'User message: The design could not compile.',
      '',
      'Traceback:\nAttributeError: missing API',
    ].join('\n'))
  })

  it('merges monotonic progress snapshots without restoring truncated or stale events', () => {
    const current = progressSnapshot({
      last_batch_sequence: 2,
      last_sequence: 2,
      events: [
        progressSnapshot().events[0]!,
        {
          sequence: 2,
          kind: 'tool_started',
          text: null,
          tool_name: 'read',
          target: 'design.py',
          is_error: null,
          occurred_at: '2026-08-16T01:00:02Z',
        },
      ],
    })
    const incoming = progressSnapshot({
      last_batch_sequence: 3,
      last_sequence: 3,
      truncated_before_sequence: 1,
      events: [{
        sequence: 3,
        kind: 'tool_finished',
        text: null,
        tool_name: 'read',
        target: 'design.py',
        is_error: false,
        occurred_at: '2026-08-16T01:00:03Z',
      }],
    })

    const merged = mergeProgressSnapshot(current, incoming)

    expect(merged.truncated_before_sequence).toBe(1)
    expect(merged.events.map(event => event.sequence)).toEqual([2, 3])
    expect(mergeProgressSnapshot(merged, current)).toBe(merged)
  })

  it('keeps the newest execution when progress execution ids differ', () => {
    const current = progressSnapshot()
    const incoming = progressSnapshot({
      execution_id: 'execution-b',
      execution_started_at: '2026-08-16T01:01:00Z',
    })

    expect(mergeProgressSnapshot(current, incoming)).toBe(incoming)
    expect(mergeProgressSnapshot(incoming, current)).toBe(incoming)
  })
})
