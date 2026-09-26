import { useEffect, useState } from 'react'
import './App.css'
import { getSessionHistory, listDatasets } from './api'
import { getCurrentUserId } from './auth'
import ChatPanel from './components/ChatPanel'
import DatasetPanel from './components/DatasetPanel'
import type { DisplayMessage } from './components/MessageBubble'
import UserBar from './components/UserBar'
import VersionPanel from './components/VersionPanel'
import type { ChatMessage, DatasetSummary } from './types'

function toDisplayMessage(m: ChatMessage): DisplayMessage {
  return {
    role: m.role,
    content: m.content,
    route: m.route,
    code: m.code,
    artifactHtml: m.artifact_html,
    pendingTransformId: m.pending_transform_id,
    error: m.error,
  }
}

function AccessDenied() {
  return (
    <div className="access-denied">
      <h1>Access denied</h1>
      <p>No user is configured for this app.</p>
      <p>
        Create <code>apps/web/.env.local</code> with your identity, then restart the dev server:
      </p>
      <pre>VITE_USER_ID=you@example.com</pre>
    </div>
  )
}

export default function App() {
  const userId = getCurrentUserId()
  const [datasets, setDatasets] = useState<DatasetSummary[]>([])
  const [selectedIds, setSelectedIds] = useState<string[]>([])
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [attachedDatasetIds, setAttachedDatasetIds] = useState<string[]>([])
  const [messages, setMessages] = useState<DisplayMessage[]>([])
  const [focusedVersionDatasetId, setFocusedVersionDatasetId] = useState<string | null>(null)

  async function refreshDatasets() {
    if (!userId) return
    const res = await listDatasets(userId)
    setDatasets(res.datasets)
  }

  useEffect(() => {
    refreshDatasets()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [userId])

  if (!userId) {
    return <AccessDenied />
  }

  function toggleSelected(id: string) {
    if (attachedDatasetIds.includes(id)) return
    setSelectedIds((prev) => (prev.includes(id) ? prev.filter((x) => x !== id) : [...prev, id]))
  }

  function handleSessionUpdate(newSessionId: string, datasetIds: string[]) {
    setSessionId(newSessionId)
    setAttachedDatasetIds(datasetIds)
    setSelectedIds([])
  }

  function handleNewChat() {
    setSessionId(null)
    setAttachedDatasetIds([])
    setMessages([])
    setSelectedIds([])
  }

  async function handleLoadSession(id: string) {
    const history = await getSessionHistory(id)
    setSessionId(history.session_id)
    setAttachedDatasetIds(history.dataset_ids)
    setMessages(history.messages.map(toDisplayMessage))
    setSelectedIds([])
    if (history.dataset_ids.length > 0) setFocusedVersionDatasetId(history.dataset_ids[0])
  }

  return (
    <div className="app-layout">
      <UserBar userId={userId} />
      <div className="app-columns">
        <DatasetPanel
          userId={userId}
          datasets={datasets}
          attachedIds={attachedDatasetIds}
          selectedIds={selectedIds}
          onToggleSelected={toggleSelected}
          onUploaded={refreshDatasets}
          onFocusVersions={setFocusedVersionDatasetId}
        />
        <ChatPanel
          userId={userId}
          sessionId={sessionId}
          attachedDatasetIds={attachedDatasetIds}
          pendingNewDatasetIds={selectedIds}
          datasets={datasets}
          messages={messages}
          onSessionUpdate={handleSessionUpdate}
          onMessagesChange={setMessages}
          onResolvedDataset={(id) => id && setFocusedVersionDatasetId(id)}
          onNewChat={handleNewChat}
          onLoadSession={handleLoadSession}
        />
        <VersionPanel datasetId={focusedVersionDatasetId} datasets={datasets} userId={userId} />
      </div>
    </div>
  )
}
