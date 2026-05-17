import { useMemo, useState } from 'react'
import type { CompactMarker, ToolEvent } from '../../types/qa'

interface Props {
  events: ToolEvent[]
  compactMarkers?: CompactMarker[]
}

interface CombinedRow {
  iteration: number
  name: string
  args_preview?: unknown
  ok?: boolean
  preview?: string
  done: boolean
}

type TimelineEntry =
  | { kind: 'iteration_header'; iteration: number }
  | { kind: 'tool'; row: CombinedRow }
  | { kind: 'compact'; marker: CompactMarker }

function combine(events: ToolEvent[]): CombinedRow[] {
  const rows: CombinedRow[] = []
  for (const e of events) {
    if (e.phase === 'call') {
      rows.push({
        iteration: e.iteration,
        name: e.name,
        args_preview: e.args_preview,
        done: false,
      })
    } else {
      const target = [...rows]
        .reverse()
        .find((r) => r.iteration === e.iteration && r.name === e.name && !r.done)
      if (target) {
        target.ok = e.ok
        target.preview = e.preview
        target.done = true
      } else {
        rows.push({
          iteration: e.iteration,
          name: e.name,
          ok: e.ok,
          preview: e.preview,
          done: true,
        })
      }
    }
  }
  return rows
}

function buildTimeline(
  rows: CombinedRow[],
  compactMarkers: CompactMarker[],
): TimelineEntry[] {
  const out: TimelineEntry[] = []
  let lastIter = -1
  let markerIdx = 0
  const sortedMarkers = [...compactMarkers].sort(
    (a, b) => a.before_iteration - b.before_iteration,
  )

  for (const row of rows) {
    // 在切换到新 iteration 前插入 compact markers
    while (
      markerIdx < sortedMarkers.length &&
      sortedMarkers[markerIdx].before_iteration <= row.iteration
    ) {
      out.push({ kind: 'compact', marker: sortedMarkers[markerIdx] })
      markerIdx += 1
    }
    if (row.iteration !== lastIter) {
      out.push({ kind: 'iteration_header', iteration: row.iteration })
      lastIter = row.iteration
    }
    out.push({ kind: 'tool', row })
  }
  // 把剩余 markers（出现在所有 row 之后）追加
  while (markerIdx < sortedMarkers.length) {
    out.push({ kind: 'compact', marker: sortedMarkers[markerIdx] })
    markerIdx += 1
  }
  return out
}

function previewJson(v: unknown, max = 200): string {
  try {
    const s = typeof v === 'string' ? v : JSON.stringify(v)
    return s.length > max ? s.slice(0, max) + '…' : s
  } catch {
    return String(v)
  }
}

export default function ToolTimeline({ events, compactMarkers = [] }: Props) {
  const rows = useMemo(() => combine(events), [events])
  const timeline = useMemo(
    () => buildTimeline(rows, compactMarkers),
    [rows, compactMarkers],
  )
  const [open, setOpen] = useState(false)

  if (rows.length === 0 && compactMarkers.length === 0) return null

  const doneCount = rows.filter((r) => r.done).length
  const total = rows.length
  const compactSuffix =
    compactMarkers.length > 0 ? ` · 📦 ${compactMarkers.length} 次压缩` : ''
  const label =
    total > 0
      ? `🔧 ${doneCount}/${total} 次工具调用${compactSuffix}`
      : `📦 ${compactMarkers.length} 次上下文压缩`

  return (
    <div className="my-2 border border-gray-200 dark:border-gray-700 rounded bg-gray-50 dark:bg-gray-800/60">
      <button
        className="w-full text-left px-3 py-1.5 text-xs text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-800 flex items-center justify-between"
        onClick={() => setOpen((v) => !v)}
      >
        <span>{label}</span>
        <span className="text-gray-400">{open ? '▲' : '▼'}</span>
      </button>
      {open && (
        <div className="px-3 py-2 border-t border-gray-200 dark:border-gray-700 space-y-2">
          {timeline.map((entry, idx) => {
            if (entry.kind === 'iteration_header') {
              return (
                <div
                  key={idx}
                  className="text-[10px] uppercase tracking-wider text-gray-400 dark:text-gray-500 pt-1"
                >
                  —— 第 {entry.iteration} 轮 ——
                </div>
              )
            }
            if (entry.kind === 'compact') {
              return (
                <div
                  key={idx}
                  className="text-xs px-2 py-1 bg-amber-50 dark:bg-amber-900/30 border border-amber-200 dark:border-amber-700 rounded text-amber-800 dark:text-amber-200 flex items-center gap-2"
                >
                  <span>📦</span>
                  <span>
                    上下文压缩：摘要 {entry.marker.summarized_turns} 条历史，
                    现存约 {entry.marker.new_input_tokens} tokens
                  </span>
                </div>
              )
            }
            const r = entry.row
            return (
              <div key={idx} className="text-xs font-mono">
                <div className="flex items-center gap-2 text-gray-700 dark:text-gray-300">
                  <span className="text-gray-400">#{r.iteration}</span>
                  <span className="font-semibold">{r.name}</span>
                  {r.done ? (
                    r.ok ? (
                      <span className="text-green-600 dark:text-green-400">✓</span>
                    ) : (
                      <span className="text-red-600 dark:text-red-400">✗</span>
                    )
                  ) : (
                    <span className="text-blue-500 animate-pulse">…</span>
                  )}
                </div>
                {r.args_preview !== undefined && (
                  <div className="pl-5 text-gray-500 dark:text-gray-400 whitespace-pre-wrap break-words">
                    args: {previewJson(r.args_preview, 200)}
                  </div>
                )}
                {r.preview && (
                  <div className="pl-5 text-gray-500 dark:text-gray-400 whitespace-pre-wrap break-words">
                    → {previewJson(r.preview, 300)}
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
