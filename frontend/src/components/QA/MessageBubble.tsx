import MarkdownRenderer from '../Wiki/MarkdownRenderer'
import type { CompactMarker, QAMessage, StopReason, ToolEvent } from '../../types/qa'
import type { CodeRef } from '../../types/wiki'
import { QACodeRefsContext } from './QACodeRefsContext'
import ToolTimeline from './ToolTimeline'

interface BaseProps {
  role: 'user' | 'assistant'
  content: string
  mode?: QAMessage['mode']
  toolEvents?: ToolEvent[]
  codeRefs?: Record<string, CodeRef>
  compactMarkers?: CompactMarker[]
  stopReason?: StopReason | null
  streaming?: boolean
}

const STOP_REASON_BANNERS: Record<Exclude<StopReason, 'completed'>, {
  text: string
  classes: string
}> = {
  max_iterations: {
    text: '⚠ 已达到工具调用上限，回答可能不完整',
    classes:
      'bg-amber-50 dark:bg-amber-900/30 border-amber-200 dark:border-amber-700 text-amber-800 dark:text-amber-200',
  },
  cancelled: {
    text: '✕ 已取消',
    classes:
      'bg-gray-50 dark:bg-gray-800/60 border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300',
  },
  model_error: {
    text: '⚠ 模型调用失败',
    classes:
      'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-700 dark:text-red-300',
  },
  compact_failed: {
    text: '⚠ 上下文压缩失败，会话已中止',
    classes:
      'bg-red-50 dark:bg-red-900/30 border-red-200 dark:border-red-700 text-red-700 dark:text-red-300',
  },
}

export default function MessageBubble({
  role,
  content,
  mode,
  toolEvents = [],
  codeRefs,
  compactMarkers,
  stopReason,
  streaming,
}: BaseProps) {
  if (role === 'user') {
    return (
      <div className="flex justify-end">
        <div className="max-w-[85%] px-3 py-2 rounded-lg bg-blue-600 text-white text-sm whitespace-pre-wrap break-words">
          {content}
        </div>
      </div>
    )
  }

  const banner =
    stopReason && stopReason !== 'completed' ? STOP_REASON_BANNERS[stopReason] : null

  return (
    <div className="flex justify-start">
      <div className="max-w-[95%] w-full px-3 py-2 rounded-lg bg-gray-100 dark:bg-gray-800 text-gray-900 dark:text-gray-100">
        {mode === 'deep' && (
          <ToolTimeline events={toolEvents} compactMarkers={compactMarkers} />
        )}
        <QACodeRefsContext.Provider value={codeRefs ?? null}>
          {content ? (
            <MarkdownRenderer content={content} />
          ) : streaming ? (
            <div className="text-gray-400 text-sm italic">思考中…</div>
          ) : null}
        </QACodeRefsContext.Provider>
        {streaming && content && (
          <span className="inline-block w-2 h-4 align-text-bottom bg-gray-400 animate-pulse" />
        )}
        {banner && (
          <div
            className={`mt-2 text-xs px-2 py-1 rounded border ${banner.classes}`}
          >
            {banner.text}
          </div>
        )}
      </div>
    </div>
  )
}
