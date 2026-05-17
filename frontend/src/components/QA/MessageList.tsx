import { useEffect, useRef } from 'react'
import { useQAStore } from '../../store/useQAStore'
import MessageBubble from './MessageBubble'

export default function MessageList() {
  const messages = useQAStore((s) => s.messages)
  const pending = useQAStore((s) => s.pendingAssistant)
  const streaming = useQAStore((s) => s.streaming)
  const streamError = useQAStore((s) => s.streamError)

  const bottomRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [messages, pending?.content, pending?.toolEvents.length])

  if (messages.length === 0 && !pending && !streamError) {
    return (
      <div className="flex-1 flex items-center justify-center text-center px-6 text-sm text-gray-500 dark:text-gray-400">
        <div>
          <div className="text-4xl mb-3">💬</div>
          <div className="mb-1 font-medium">向项目提问</div>
          <div className="text-xs">
            快速模式秒级作答；深度模式可见 Agent 检索路径
          </div>
        </div>
      </div>
    )
  }

  return (
    <div className="flex-1 overflow-y-auto px-3 py-4 space-y-3">
      {messages.map((m) => (
        <MessageBubble
          key={m.id}
          role={m.role}
          content={m.content}
          mode={m.mode}
          toolEvents={m.tool_events}
          codeRefs={m.code_refs}
          compactMarkers={m.compact_markers}
          stopReason={m.stop_reason}
        />
      ))}
      {pending && (
        <MessageBubble
          role="assistant"
          content={pending.content}
          mode={pending.mode}
          toolEvents={pending.toolEvents}
          codeRefs={pending.codeRefs}
          compactMarkers={pending.compactMarkers}
          stopReason={pending.stopReason}
          streaming={streaming}
        />
      )}
      {streamError && (
        <div className="text-xs px-3 py-2 bg-red-50 dark:bg-red-900/30 border border-red-200 dark:border-red-700 text-red-700 dark:text-red-300 rounded">
          错误：{streamError}
        </div>
      )}
      <div ref={bottomRef} />
    </div>
  )
}
