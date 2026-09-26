import { useState } from 'react'
import { confirmTransform, rejectTransform } from '../api'
import type { DiffSummary } from '../types'

export interface DisplayMessage {
  role: 'user' | 'assistant'
  content: string
  route?: string | null
  code?: string | null
  artifactHtml?: string | null
  diff?: DiffSummary | null
  pendingTransformId?: string | null
  rememberedFact?: string | null
  error?: string | null
}

interface Props {
  message: DisplayMessage
  userId: string
  onTransformResolved: (pendingId: string, status: 'confirmed' | 'rejected') => void
}

export default function MessageBubble({ message, userId, onTransformResolved }: Props) {
  const [resolving, setResolving] = useState(false)
  const [resolved, setResolved] = useState<'confirmed' | 'rejected' | null>(null)
  const [resolveError, setResolveError] = useState<string | null>(null)

  async function handleConfirm() {
    if (!message.pendingTransformId) return
    setResolving(true)
    setResolveError(null)
    try {
      await confirmTransform(message.pendingTransformId, userId)
      setResolved('confirmed')
      onTransformResolved(message.pendingTransformId, 'confirmed')
    } catch (e) {
      setResolveError(e instanceof Error ? e.message : String(e))
    } finally {
      setResolving(false)
    }
  }

  async function handleReject() {
    if (!message.pendingTransformId) return
    setResolving(true)
    setResolveError(null)
    try {
      await rejectTransform(message.pendingTransformId)
      setResolved('rejected')
      onTransformResolved(message.pendingTransformId, 'rejected')
    } catch (e) {
      setResolveError(e instanceof Error ? e.message : String(e))
    } finally {
      setResolving(false)
    }
  }

  return (
    <div className={`message ${message.role}`}>
      <div className="message-header">
        <span className="message-role">{message.role === 'user' ? 'You' : 'Agent'}</span>
        {message.route && <span className="badge route-badge">{message.route}</span>}
      </div>
      <div className="message-content">{message.content}</div>
      {message.error && <div className="error-text">{message.error}</div>}

      {message.code && (
        <details className="code-block">
          <summary>Show generated code</summary>
          <pre>
            <code>{message.code}</code>
          </pre>
        </details>
      )}

      {message.artifactHtml && (
        <iframe
          className="artifact-frame"
          sandbox="allow-scripts"
          srcDoc={message.artifactHtml}
          title="artifact"
        />
      )}

      {message.pendingTransformId && message.diff && (
        <div className="transform-card">
          <div className="transform-diff">{message.diff.summary}</div>
          <div className="transform-counts">
            columns: {message.diff.column_count_before} → {message.diff.column_count_after} · rows:{' '}
            {message.diff.row_count_before} → {message.diff.row_count_after}
          </div>
          {resolved ? (
            <div className={`transform-status ${resolved}`}>{resolved === 'confirmed' ? '✓ Applied' : '✗ Discarded'}</div>
          ) : (
            <div className="transform-actions">
              <button disabled={resolving} onClick={handleConfirm} className="confirm-button">
                Confirm
              </button>
              <button disabled={resolving} onClick={handleReject} className="reject-button">
                Reject
              </button>
            </div>
          )}
          {resolveError && <p className="error-text">{resolveError}</p>}
        </div>
      )}
    </div>
  )
}
