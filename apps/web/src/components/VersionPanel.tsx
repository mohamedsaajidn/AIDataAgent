import { useEffect, useState } from 'react'
import { exportUrl, listVersions, setActiveVersion } from '../api'
import type { DatasetSummary, VersionsResponse } from '../types'

interface Props {
  datasetId: string | null
  datasets: DatasetSummary[]
}

export default function VersionPanel({ datasetId, datasets }: Props) {
  const [data, setData] = useState<VersionsResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [switching, setSwitching] = useState<string | null>(null)

  const dataset = datasets.find((d) => d.id === datasetId)

  async function load() {
    if (!datasetId) return
    setLoading(true)
    setError(null)
    try {
      setData(await listVersions(datasetId))
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    load()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasetId])

  async function handleMakeActive(versionId: string) {
    if (!datasetId) return
    setSwitching(versionId)
    setError(null)
    try {
      await setActiveVersion(datasetId, versionId)
      await load()
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e))
    } finally {
      setSwitching(null)
    }
  }

  if (!datasetId) {
    return (
      <div className="panel version-panel">
        <h2>Versions</h2>
        <p className="chat-empty">Click "versions" next to a dataset, or ask a question, to see its history here.</p>
      </div>
    )
  }

  return (
    <div className="panel version-panel">
      <h2>Versions {dataset && <span className="version-panel-title">— {dataset.name}</span>}</h2>
      <button className="link-button" onClick={load} disabled={loading}>
        refresh
      </button>
      {error && <p className="error-text">{error}</p>}

      <ul className="version-list">
        {data?.versions.map((v) => {
          const isActive = v.id === data.active_version_id
          return (
            <li key={v.id} className={isActive ? 'version-item active' : 'version-item'}>
              <div className="version-item-header">
                <span className="version-number">v{v.version_number}</span>
                {isActive && <span className="badge">active</span>}
                <span className="version-source">{v.source}</span>
              </div>
              <div className="version-meta">
                {v.row_count} rows × {v.column_count} cols
              </div>
              {v.diff_summary && <div className="version-diff">{v.diff_summary}</div>}
              {v.generated_code && (
                <details className="code-block">
                  <summary>Show transformation code</summary>
                  <pre>
                    <code>{v.generated_code}</code>
                  </pre>
                </details>
              )}
              <div className="version-actions">
                {!isActive && (
                  <button disabled={switching === v.id} onClick={() => handleMakeActive(v.id)}>
                    {switching === v.id ? 'Switching…' : 'Make active (undo/redo to here)'}
                  </button>
                )}
                <a href={exportUrl(v.dataset_id, 'csv', v.id)}>CSV</a>
                <a href={exportUrl(v.dataset_id, 'xlsx', v.id)}>XLSX</a>
                <a href={exportUrl(v.dataset_id, 'parquet', v.id)}>Parquet</a>
              </div>
            </li>
          )
        })}
      </ul>
    </div>
  )
}
