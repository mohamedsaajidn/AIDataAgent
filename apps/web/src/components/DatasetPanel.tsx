import { useRef, useState } from 'react'
import { uploadDataset } from '../api'
import type { DatasetSummary } from '../types'

interface Props {
  userId: string
  datasets: DatasetSummary[]
  attachedIds: string[]
  selectedIds: string[]
  onToggleSelected: (id: string) => void
  onUploaded: () => void
  onFocusVersions: (id: string) => void
}

export default function DatasetPanel({
  userId,
  datasets,
  attachedIds,
  selectedIds,
  onToggleSelected,
  onUploaded,
  onFocusVersions,
}: Props) {
  const [uploading, setUploading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return
    setUploading(true)
    setError(null)
    try {
      for (const file of Array.from(files)) {
        await uploadDataset(file, userId)
      }
      onUploaded()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setUploading(false)
      if (fileInput.current) fileInput.current.value = ''
    }
  }

  return (
    <div className="panel dataset-panel">
      <h2>Datasets</h2>
      <p className="panel-hint">Only datasets you've uploaded are shown here.</p>
      <label className="upload-drop">
        <input
          ref={fileInput}
          type="file"
          accept=".csv,.xlsx"
          multiple
          onChange={(e) => handleFiles(e.target.files)}
          disabled={uploading}
        />
        {uploading ? 'Uploading…' : 'Click to upload CSV/XLSX (multiple allowed)'}
      </label>
      {error && <p className="error-text">{error}</p>}

      <ul className="dataset-list">
        {datasets.map((d) => {
          const isAttached = attachedIds.includes(d.id)
          const isSelected = selectedIds.includes(d.id)
          return (
            <li key={d.id} className={isAttached ? 'dataset-item attached' : 'dataset-item'}>
              <label>
                <input
                  type="checkbox"
                  checked={isSelected || isAttached}
                  disabled={isAttached}
                  onChange={() => onToggleSelected(d.id)}
                />
                <span className="dataset-name">{d.name}</span>
              </label>
              <span className="dataset-meta">
                {d.row_count ?? '?'} rows × {d.column_count ?? '?'} cols
              </span>
              <button className="link-button" onClick={() => onFocusVersions(d.id)}>
                versions
              </button>
              {isAttached && <span className="badge">in chat</span>}
            </li>
          )
        })}
        {datasets.length === 0 && <li className="dataset-empty">No datasets uploaded yet.</li>}
      </ul>
    </div>
  )
}
