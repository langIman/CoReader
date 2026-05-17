import type { CodeRef } from './wiki'

export type QAMode = 'fast' | 'deep'

// 与后端 backend/services/agent/events.py 的 StopReason 保持一致
export type StopReason =
  | 'completed'
  | 'max_iterations'
  | 'cancelled'
  | 'model_error'
  | 'compact_failed'

export interface ToolEvent {
  iteration: number
  name: string
  args?: unknown
  args_preview?: unknown
  ok?: boolean
  preview?: string
  phase: 'call' | 'result'
}

// 一轮压缩边界标记（后端 Agent 触发 autocompact 后 yield）
export interface CompactMarker {
  // 出现在哪一轮压缩前；用于在工具时间线里挂位置
  // （-1 表示发生在主循环开头，未跑到任何轮次时——理论上不会，但保留兜底）
  before_iteration: number
  summarized_turns: number
  new_input_tokens: number
}

export interface QAMessage {
  id: number
  conversation_id: string
  role: 'user' | 'assistant'
  content: string
  mode?: QAMode | null
  tool_events: ToolEvent[]
  code_refs: Record<string, CodeRef>
  stop_reason?: StopReason | null
  compact_markers?: CompactMarker[]
  created_at: string
}

export interface Conversation {
  id: string
  project_name: string
  title: string
  created_at: string
  updated_at: string
}

export interface ConversationDetail extends Conversation {
  messages: QAMessage[]
}

export interface QARequest {
  project_name: string
  conversation_id?: string | null
  question: string
  mode: QAMode
}

export type SSEEvent =
  | { event: 'start'; data: { conversation_id: string; user_message_id: number; mode: QAMode } }
  | { event: 'token'; data: { delta: string } }
  | {
      event: 'tool_call'
      data: { iteration: number; name: string; args_preview: unknown }
    }
  | {
      event: 'tool_result'
      data: { iteration: number; name: string; ok: boolean; preview: string }
    }
  | {
      event: 'compact_boundary'
      data: { summarized_turns: number; new_input_tokens: number }
    }
  | { event: 'code_refs'; data: { refs: Record<string, CodeRef> } }
  | {
      event: 'done'
      data: { assistant_message_id: number; content?: string; stop_reason?: StopReason }
    }
  | { event: 'error'; data: { message: string } }
