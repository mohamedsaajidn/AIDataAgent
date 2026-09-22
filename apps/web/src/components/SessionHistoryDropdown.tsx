import { useEffect, useState } from 'react'
import { listSessions } from '../api'
import type { DatasetSummary, SessionSummary } from '../types'

interface Props {
  userId: string
  currentSessionId: string | null
  datasets: DatasetSummary[]
  onPick: (sessionId: string) => void
}

export default function SessionHistoryDropdown({ userId, currentSessionId, datasets, onPick }: Props) {
  const [open, setOpen] = useState(false)
  const [sessions, setSessions] = useState<SessionSummary[]>([])
  const [loading, setLoading] = useState(false)

  const nameFor = (id: string) => datasets.find((d) => d.id === id)?.name ?? id

  async function load() {
    setLoading(true)
    try {
      const res = await listSessions(userId)
      setSessions(res.sessions)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    if (open) load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, userId])

  return (
    <div className="session-history">
      <button className="link-button" onClick={() => setOpen((o) => !o)}>
        {open ? 'Hide past chats' : 'Past chats'}
      </button>
      {open && (
        <div className="session-history-dropdown">
          {loading && <p className="chat-empty">Loading…</p>}
          {!loading && sessions.length === 0 && <p className="chat-empty">No past chats yet.</p>}
          <ul>
            {sessions.map((s) => (
              <li key={s.session_id} className={s.session_id === currentSessionId ? 'active' : ''}>
                <button
                  onClick={() => {
                    onPick(s.session_id)
                    setOpen(false)
                  }}
                >
                  <span className="session-datasets">{s.dataset_ids.map(nameFor).join(', ') || '(no datasets)'}</span>
                  <span className="session-question">{s.last_question ?? '(no messages)'}</span>
                  <span className="session-meta">
                    {s.message_count} messages · {s.created_at ? new Date(s.created_at).toLocaleString() : ''}
                  </span>
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
