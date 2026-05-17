import { useEffect, useMemo, useRef } from 'react'
import type { WikiProgressEvent, WikiProgressStage } from '../../types/wiki'

const STAGE_LABEL: Record<WikiProgressStage, string> = {
  file_summary: '文件摘要',
  folder_summary: '文件夹摘要',
  project_summary: '项目摘要',
  module_split: '模块划分',
  outline: '大纲生成',
  module_page: '模块页',
  chapter_page: '章节页',
  topic_page: '专题页',
  overview: '概览页',
}

function formatDuration(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.round(s - m * 60)
  return `${m}:${String(rs).padStart(2, '0')}`
}

interface Props {
  events: WikiProgressEvent[]
  /** 父级 1s tick 传入用于 running 事件实时跳秒 */
  nowMs: number
  /** events 最后一次到达本地的客户端时间戳；用于在 ev.duration_ms 上推算
   *  running 事件的"现在"耗时，避免依赖跨机器墙钟。 */
  eventsReceivedAt: number
}

export default function WikiProgressTimeline({ events, nowMs, eventsReceivedAt }: Props) {
  const scrollerRef = useRef<HTMLDivElement>(null)
  const stickToBottomRef = useRef(true)

  // 折叠：每个 event_id 取最新一条；按 started_at 升序。
  // 防御性过滤：缺关键字段的脏事件会让 sort 抛 TypeError，进而把整棵 React
  // 树带崩——后端任何路径漏字段都应在此被无害忽略，而不是反弹到上传页。
  const folded = useMemo(() => {
    const map = new Map<string, WikiProgressEvent>()
    for (const ev of events) {
      if (!ev || typeof ev.event_id !== 'string' || typeof ev.started_at !== 'string') {
        console.warn('[WikiProgressTimeline] 丢弃缺字段事件:', ev)
        continue
      }
      map.set(ev.event_id, ev)
    }
    return Array.from(map.values()).sort((a, b) => a.started_at.localeCompare(b.started_at))
  }, [events])

  // 用户若手动向上滚动则停止自动跟随
  const onScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24
    stickToBottomRef.current = atBottom
  }

  useEffect(() => {
    if (!stickToBottomRef.current) return
    const el = scrollerRef.current
    if (el) el.scrollTop = el.scrollHeight
  }, [folded.length])

  if (folded.length === 0) {
    return (
      <p className="text-sm text-gray-500 dark:text-gray-400">
        正在准备…
      </p>
    )
  }

  // 概览统计
  const counts = folded.reduce(
    (acc, ev) => {
      acc[ev.status] = (acc[ev.status] ?? 0) + 1
      return acc
    },
    {} as Record<string, number>,
  )

  return (
    <div className="w-full max-w-2xl">
      <div className="flex justify-between items-center mb-2 px-2 text-xs text-gray-500 dark:text-gray-400">
        <span>
          已完成 <span className="font-mono text-gray-700 dark:text-gray-200">{counts.done ?? 0}</span>
          {' · '}
          进行中 <span className="font-mono text-blue-600 dark:text-blue-300">{counts.running ?? 0}</span>
          {(counts.failed ?? 0) > 0 && (
            <>
              {' · '}
              失败 <span className="font-mono text-red-600 dark:text-red-400">{counts.failed}</span>
            </>
          )}
        </span>
        <span>共 {folded.length} 项</span>
      </div>
      <div
        ref={scrollerRef}
        onScroll={onScroll}
        className="max-h-72 overflow-y-auto rounded-lg border border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-900/40 text-left"
      >
        <ul className="divide-y divide-gray-100 dark:divide-gray-800">
          {folded.map((ev) => (
            <li key={ev.event_id} className="px-3 py-1.5 flex items-center gap-2 text-xs font-mono">
              <StatusIcon status={ev.status} />
              <span className="text-gray-500 dark:text-gray-400 w-20 shrink-0">{STAGE_LABEL[ev.stage] ?? ev.stage}</span>
              <span className="flex-1 truncate text-gray-800 dark:text-gray-200" title={ev.item ?? ''}>
                {ev.item ?? <span className="text-gray-400">—</span>}
              </span>
              <DurationCell ev={ev} nowMs={nowMs} eventsReceivedAt={eventsReceivedAt} />
            </li>
          ))}
        </ul>
      </div>
    </div>
  )
}

function StatusIcon({ status }: { status: WikiProgressEvent['status'] }) {
  if (status === 'done') return <span className="text-green-600 dark:text-green-400">✓</span>
  if (status === 'failed') return <span className="text-red-600 dark:text-red-400">✗</span>
  // running
  return <span className="text-blue-600 dark:text-blue-300 animate-pulse">●</span>
}

function DurationCell({
  ev, nowMs, eventsReceivedAt,
}: { ev: WikiProgressEvent; nowMs: number; eventsReceivedAt: number }) {
  if (ev.status === 'running') {
    // 用服务器侧填的 duration_ms（perf_counter 单调时钟，准确），加上"自上次
    // poll 以来的客户端流逝时间"做平滑外推，让秒数每 1s 跳动而不是只在 2s
    // poll 到来时跳一下。
    const serverElapsed = ev.duration_ms ?? 0
    const localExtrap = Math.max(0, nowMs - eventsReceivedAt)
    return (
      <span className="text-blue-600 dark:text-blue-300 tabular-nums w-14 text-right shrink-0">
        {formatDuration(serverElapsed + localExtrap)}
      </span>
    )
  }
  if (ev.duration_ms != null) {
    return (
      <span
        className={`tabular-nums w-14 text-right shrink-0 ${
          ev.status === 'failed' ? 'text-red-600 dark:text-red-400' : 'text-gray-500 dark:text-gray-400'
        }`}
        title={ev.error ?? undefined}
      >
        {formatDuration(ev.duration_ms)}
      </span>
    )
  }
  return <span className="w-14 shrink-0" />
}
