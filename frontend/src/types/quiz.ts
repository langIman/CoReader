// 与后端 backend/models/quiz_models.py 保持一致

export type QuizMode = 'history' | 'page' | 'project'

export type QuizStatus = 'generating' | 'ready' | 'done'

// 前端状态机：drawer 内的展示阶段（与后端 QuizStatus 不同）
export type QuizPhase =
  | 'mode_select' // 选择测验模式
  | 'generating'  // SSE 生成中
  | 'quizzing'    // 答题中
  | 'result'      // 得分总结

export interface QuizOption {
  key: string // 'A' | 'B' | 'C' | 'D'
  text: string
  explanation: string
}

export interface QuizCodeRef {
  file: string
  line_start: number
  line_end: number
}

export interface QuizQuestion {
  index: number
  question_text: string
  options: QuizOption[]
  correct_key: string
  code_ref: QuizCodeRef | null
}

export interface QuizSession {
  id: string
  project_name: string
  mode: QuizMode
  source_id: string | null
  title: string
  status: QuizStatus
  score: number
  answered_count: number
  created_at: string
}

export interface QuizSessionDetail extends QuizSession {
  questions: QuizQuestion[]
  answers: Record<number, string> // index → chosen_key
}

export interface QuizGenerateRequest {
  project_name: string
  mode: QuizMode
  source_id?: string | null
}

export interface QuizAnswerResult {
  is_correct: boolean
  correct_key: string
}

// SSE 事件（POST /api/quiz/generate）
export type QuizSSEEvent =
  | { event: 'thinking'; data: { tool: string; status: string } }
  | {
      event: 'question'
      data: {
        index: number
        question_text: string
        options: QuizOption[]
        correct_key: string
        code_ref: QuizCodeRef | null
      }
    }
  | { event: 'done'; data: { session_id: string; title: string } }
  | { event: 'error'; data: { message: string } }
