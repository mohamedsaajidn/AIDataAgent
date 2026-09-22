import { useState } from 'react'
import { askQuestion } from '../api'
import type { DatasetSummary } from '../types'
import MessageBubble, { type DisplayMessage } from './MessageBubble'
import SessionHistoryDropdown from './SessionHistoryDropdown'

interface Props {
  userId: string
  sessionId: string | null
  attachedDatasetIds: string[]
  pendingNewDatasetIds: string[]
  datasets: DatasetSummary[]
  messages: DisplayMessage[]
  onSessionUpdate: (sessionId: string, datasetIds: string[]) => void
  onMessagesChange: (updater: (prev: DisplayMessage[]) => DisplayMessage[]) => void
  onResolvedDataset: (id: string | null) => void
  onNewChat: () => void
  onLoadSession: (sessionId: string) => void
}

export default function ChatPanel({
  userId,
  sessionId,
  attachedDatasetIds,
  pendingNewDatasetIds,
  datasets,
  messages,
  onSessionUpdate,
  onMessagesChange,
  onResolvedDataset,
  onNewChat,
  onLoadSession,
}: Props) {
  const [question, setQuestion] = useState('')
  const [sending, setSending] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const nameFor = (id: string) => datasets.find((d) => d.id === id)?.name ?? id

  async function handleSend() {
    const trimmed = question.trim()
    if (!trimmed) return
    if (!sessionId && pendingNewDatasetIds.length === 0) {
      setError('Select at least one dataset to start a chat.')
      return
    }
    setSending(true)
    setError(null)
    onMessagesChange((prev) => [...prev, { role: 'user', content: trimmed }])
    setQuestion('')

    try {
      const res = await askQuestion({
        datasetIds: pendingNewDatasetIds,
        question: trimmed,
        userId,
        sessionId,
      })
      onSessionUpdate(res.session_id, res.dataset_ids)
      onResolvedDataset(res.resolved_dataset_id)
      onMessagesChange((prev) => [
        ...prev,
        {
          role: 'assistant',
          content: res.answer,
          route: res.route,
          code: res.code,
          artifactHtml: res.artifact_html,
          diff: res.diff,
          pendingTransformId: res.pending_transform_id,
          rememberedFact: res.remembered_fact,
          error: res.error,
        },
      ])
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSending(false)
    }
  }

  function handleTransformResolved() {
    // Confirming/rejecting changes dataset version state elsewhere in the UI
    // (version history, export) - nothing to do to the message list itself,
    // the card already reflects its own resolved status locally.
  }

  return (
    <div className="panel chat-panel">
      <div className="chat-header">
        <h2>Chat</h2>
        <div className="chat-header-right">
          {attachedDatasetIds.length > 0 && (
            <span className="attached-list">Attached: {attachedDatasetIds.map(nameFor).join(', ')}</span>
          )}
          <SessionHistoryDropdown
            userId={userId}
            currentSessionId={sessionId}
            datasets={datasets}
            onPick={onLoadSession}
          />
          <button className="link-button" onClick={onNewChat}>
            + New chat
          </button>
        </div>
      </div>

      <div className="message-list">
        {messages.length === 0 && (
          <p className="chat-empty">
            {sessionId
              ? 'No messages yet.'
              : 'Check one or more datasets on the left, then ask a question to start a new chat.'}
          </p>
        )}
        {messages.map((m, i) => (
          <MessageBubble key={i} message={m} onTransformResolved={handleTransformResolved} />
        ))}
      </div>

      {error && <p className="error-text">{error}</p>}

      <div className="chat-input-row">
        <textarea
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault()
              handleSend()
            }
          }}
          placeholder="Ask a question, request a transform/join/chart/EDA, or correct something…"
          disabled={sending}
        />
        <button onClick={handleSend} disabled={sending}>
          {sending ? 'Thinking…' : 'Send'}
        </button>
      </div>
    </div>
  )
}
