/// <reference lib="webworker" />

import { CollisionMeshAnalyzer } from './collisionNarrowPhaseCore'
import type {
  CollisionNarrowPhaseMatch,
  CollisionNarrowPhaseRequest,
  CollisionNarrowPhaseResponse,
} from './collisionNarrowPhase.types'

const worker = self as DedicatedWorkerGlobalScope

worker.onmessage = (event: MessageEvent<CollisionNarrowPhaseRequest>) => {
  if (event.data.type !== 'analyze') return

  let analyzer: CollisionMeshAnalyzer | null = null
  try {
    analyzer = new CollisionMeshAnalyzer(event.data.meshes)
    const matches: CollisionNarrowPhaseMatch[] = []
    let processed = 0

    const processBatch = () => {
      if (!analyzer) return
      try {
        const batchStartedAt = performance.now()
        const batchMatches: CollisionNarrowPhaseMatch[] = []
        do {
          const candidate = event.data.candidates[processed]
          if (!candidate) break
          const match = analyzer.analyzeCandidate(candidate)
          if (match) {
            matches.push(match)
            batchMatches.push(match)
          }
          processed += 1
        } while (
          processed < event.data.candidates.length
          && performance.now() - batchStartedAt < 12
        )

        const progress: CollisionNarrowPhaseResponse = {
          type: 'progress',
          processed,
          total: event.data.candidates.length,
          matches: batchMatches,
        }
        worker.postMessage(progress)

        if (processed < event.data.candidates.length) {
          setTimeout(processBatch, 0)
          return
        }

        analyzer.dispose()
        analyzer = null
        const response: CollisionNarrowPhaseResponse = { type: 'complete', matches }
        worker.postMessage(response)
      } catch (error) {
        analyzer?.dispose()
        analyzer = null
        const response: CollisionNarrowPhaseResponse = {
          type: 'error',
          message: error instanceof Error ? error.message : 'Mesh collision analysis failed',
        }
        worker.postMessage(response)
      }
    }

    processBatch()
  } catch (error) {
    analyzer?.dispose()
    const response: CollisionNarrowPhaseResponse = {
      type: 'error',
      message: error instanceof Error ? error.message : 'Mesh collision analysis failed',
    }
    worker.postMessage(response)
  }
}

export {}
