import { create } from 'zustand'
import type {
  QuizGenerateRequest,
  QuizMode,
  QuizPhase,
  QuizQuestion,
  QuizSession,
} from '../types/quiz'
import {
  deleteQuizSession,
  getQuizSession,
  listQuizSessions,
  streamGenerateQuiz,
  submitQuizAnswer,
} from '../services/quizApi'
import { useLayoutStore } from './useLayoutStore'

interface QuizStore {
  // 状态机
  phase: QuizPhase

  // 模式预设（从 wiki 页打开时填）
  presetMode: QuizMode
  presetSourceId: string | null
  presetSourceTitle: string | null

  // 生成阶段
  thinkingStatus: string
  generatedCount: number
  generateError: string | null
  abortController: AbortController | null

  // 答题阶段
  sessionId: string | null
  sessionTitle: string
  sessionMode: QuizMode | null
  questions: QuizQuestion[]
  answers: Record<number, string> // index → chosen_key
  revealed: Record<number, boolean> // index → 是否已展开解析
  currentIndex: number

  // 历史侧栏
  sessions: QuizSession[]
  sessionsLoaded: boolean
  showHistory: boolean

  // ─────────── actions ───────────

  // 入口 1：从 wiki 页面 Header 按钮打开（page 模式预选 + sourceId）
  openForPage: (pageId: string, pageTitle: string) => void
  // 入口 2：从全局按钮打开（默认 project 模式）
  openForGlobal: () => void

  setPresetMode: (mode: QuizMode) => void

  // 抽屉打开时切换 wiki 当前页，同步 page 模式的预选 sourceId（仅 mode_select 阶段生效）
  syncPageContext: (pageId: string | null, pageTitle: string | null) => void

  // 生成
  startGenerate: (req: QuizGenerateRequest) => Promise<void>
  cancelGenerate: () => void

  // 答题
  selectAnswer: (index: number, key: string) => Promise<void>
  goTo: (index: number) => void
  goNext: () => void

  // 历史
  loadSessions: (projectName: string) => Promise<void>
  selectSession: (sessionId: string) => Promise<void>
  removeSession: (sessionId: string, projectName: string) => Promise<void>
  toggleHistory: () => void

  // 重置
  resetToModeSelect: () => void
  reset: () => void
}

export const useQuizStore = create<QuizStore>((set, get) => ({
  phase: 'mode_select',

  presetMode: 'project',
  presetSourceId: null,
  presetSourceTitle: null,

  thinkingStatus: '',
  generatedCount: 0,
  generateError: null,
  abortController: null,

  sessionId: null,
  sessionTitle: '',
  sessionMode: null,
  questions: [],
  answers: {},
  revealed: {},
  currentIndex: 0,

  sessions: [],
  sessionsLoaded: false,
  showHistory: false,

  // ─────────── 入口 ───────────

  openForPage: (pageId, pageTitle) => {
    set({
      phase: 'mode_select',
      presetMode: 'page',
      presetSourceId: pageId,
      presetSourceTitle: pageTitle,
      generateError: null,
    })
    useLayoutStore.getState().openTab('quiz')
  },

  openForGlobal: () => {
    set({
      phase: 'mode_select',
      presetMode: 'project',
      presetSourceId: null,
      presetSourceTitle: null,
      generateError: null,
    })
    useLayoutStore.getState().openTab('quiz')
  },

  setPresetMode: (mode) => set({ presetMode: mode }),

  syncPageContext: (pageId, pageTitle) => {
    // 只在 mode_select 阶段同步，避免打扰已开始的测验
    if (get().phase !== 'mode_select') return
    const cur = get()
    if (cur.presetSourceId === pageId && cur.presetSourceTitle === pageTitle) return
    set({ presetSourceId: pageId, presetSourceTitle: pageTitle })
  },

  // ─────────── 生成 ───────────

  startGenerate: async (req) => {
    if (get().phase === 'generating') return
    const ctrl = new AbortController()
    set({
      phase: 'generating',
      thinkingStatus: '准备中...',
      generatedCount: 0,
      generateError: null,
      abortController: ctrl,
      questions: [],
      answers: {},
      revealed: {},
      currentIndex: 0,
      sessionId: null,
      sessionTitle: '',
      sessionMode: req.mode,
    })

    try {
      for await (const evt of streamGenerateQuiz(req, ctrl.signal)) {
        switch (evt.event) {
          case 'thinking': {
            set({ thinkingStatus: evt.data.status })
            break
          }
          case 'question': {
            set((s) => ({
              questions: [...s.questions, evt.data],
              generatedCount: s.generatedCount + 1,
            }))
            break
          }
          case 'done': {
            set({
              phase: 'quizzing',
              sessionId: evt.data.session_id,
              sessionTitle: evt.data.title,
              currentIndex: 0,
              abortController: null,
            })
            break
          }
          case 'error': {
            set({
              phase: 'mode_select',
              generateError: evt.data.message,
              abortController: null,
            })
            break
          }
        }
      }
    } catch (e) {
      if (ctrl.signal.aborted) {
        set({ phase: 'mode_select', abortController: null })
      } else {
        set({
          phase: 'mode_select',
          generateError: e instanceof Error ? e.message : '测验生成失败',
          abortController: null,
        })
      }
    }
  },

  cancelGenerate: () => {
    const ctrl = get().abortController
    if (ctrl) {
      try {
        ctrl.abort()
      } catch {
        // ignore
      }
    }
    set({ abortController: null })
  },

  // ─────────── 答题 ───────────

  selectAnswer: async (index, key) => {
    const sessionId = get().sessionId
    if (!sessionId) return
    if (get().revealed[index]) return // 已揭晓不允许改答

    // 立即本地揭晓（前端已知 correct_key，无需等服务器）
    set((s) => ({
      answers: { ...s.answers, [index]: key },
      revealed: { ...s.revealed, [index]: true },
    }))

    // 异步持久化（失败不阻塞 UI）
    try {
      await submitQuizAnswer(sessionId, index, key)
    } catch (e) {
      console.warn('[quiz] submit answer failed:', e)
    }
  },

  goTo: (index) => {
    const len = get().questions.length
    if (index < 0 || index >= len) return
    set({ currentIndex: index })
  },

  goNext: () => {
    const { currentIndex, questions } = get()
    if (currentIndex >= questions.length - 1) {
      set({ phase: 'result' })
    } else {
      set({ currentIndex: currentIndex + 1 })
    }
  },

  // ─────────── 历史 ───────────

  loadSessions: async (projectName) => {
    try {
      const list = await listQuizSessions(projectName)
      set({ sessions: list, sessionsLoaded: true })
    } catch (e) {
      console.warn('[quiz] loadSessions failed:', e)
      set({ sessions: [], sessionsLoaded: true })
    }
  },

  selectSession: async (sessionId) => {
    try {
      const detail = await getQuizSession(sessionId)
      // 重建 revealed：已经提交过答案的都视为已揭晓
      const revealed: Record<number, boolean> = {}
      for (const idx of Object.keys(detail.answers)) {
        revealed[Number(idx)] = true
      }
      const allAnswered = detail.questions.length > 0
        && detail.questions.every((q) => detail.answers[q.index] != null)
      set({
        phase: allAnswered ? 'result' : 'quizzing',
        sessionId: detail.id,
        sessionTitle: detail.title,
        sessionMode: detail.mode,
        questions: detail.questions,
        answers: detail.answers,
        revealed,
        currentIndex: 0,
        showHistory: false,
        generateError: null,
      })
    } catch (e) {
      console.warn('[quiz] selectSession failed:', e)
    }
  },

  removeSession: async (sessionId, projectName) => {
    try {
      await deleteQuizSession(sessionId)
      // 如果当前正在看这个 session，回到模式选择
      const isCurrent = get().sessionId === sessionId
      set((s) => ({
        sessions: s.sessions.filter((x) => x.id !== sessionId),
        ...(isCurrent
          ? {
              phase: 'mode_select' as QuizPhase,
              sessionId: null,
              questions: [],
              answers: {},
              revealed: {},
            }
          : {}),
      }))
      // 后台同步刷新
      void get().loadSessions(projectName)
    } catch (e) {
      console.warn('[quiz] removeSession failed:', e)
    }
  },

  toggleHistory: () => set((s) => ({ showHistory: !s.showHistory })),

  // ─────────── 重置 ───────────

  resetToModeSelect: () => {
    get().cancelGenerate()
    set({
      phase: 'mode_select',
      sessionId: null,
      sessionTitle: '',
      sessionMode: null,
      questions: [],
      answers: {},
      revealed: {},
      currentIndex: 0,
      thinkingStatus: '',
      generatedCount: 0,
      generateError: null,
    })
  },

  reset: () => {
    get().cancelGenerate()
    useLayoutStore.getState().closeTab('quiz')
    set({
      phase: 'mode_select',
      presetMode: 'project',
      presetSourceId: null,
      presetSourceTitle: null,
      sessionId: null,
      sessionTitle: '',
      sessionMode: null,
      questions: [],
      answers: {},
      revealed: {},
      currentIndex: 0,
      thinkingStatus: '',
      generatedCount: 0,
      generateError: null,
      abortController: null,
      sessions: [],
      sessionsLoaded: false,
      showHistory: false,
    })
  },
}))
