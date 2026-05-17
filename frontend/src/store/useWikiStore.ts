import { create } from 'zustand'
import type { CodeRef, WikiDocument, WikiPage, WikiProgressEvent, WikiTaskStatus } from '../types/wiki'
import { getPersistedProject, getWikiDocument } from '../services/api'
import { useLayoutStore } from './useLayoutStore'

interface CodeDrawerState {
  open: boolean
  ref: CodeRef | null
}

const PROJECT_NAME_STORAGE_KEY = 'coreader.wiki.projectName'
const NAV_WIDTH_STORAGE_KEY = 'coreader.wiki.navWidthPx'
// 进行中的 wiki 生成任务持久化：刷新/崩溃后能恢复轮询，不丢进度
const PENDING_TASK_STORAGE_KEY = 'coreader.wiki.pendingTask'
const NAV_WIDTH_MIN = 180
const NAV_WIDTH_MAX = 480
const NAV_WIDTH_DEFAULT = 256

function persistProjectName(name: string | null) {
  try {
    if (name) localStorage.setItem(PROJECT_NAME_STORAGE_KEY, name)
    else localStorage.removeItem(PROJECT_NAME_STORAGE_KEY)
  } catch {
    // ignore
  }
}

function loadPersistedProjectName(): string | null {
  try {
    return localStorage.getItem(PROJECT_NAME_STORAGE_KEY)
  } catch {
    return null
  }
}

interface PendingTask {
  task_id: string
  project_name: string
  started_at: number
}

function persistPendingTask(task: PendingTask | null) {
  try {
    if (task) localStorage.setItem(PENDING_TASK_STORAGE_KEY, JSON.stringify(task))
    else localStorage.removeItem(PENDING_TASK_STORAGE_KEY)
  } catch {
    // ignore
  }
}

function loadPendingTask(): PendingTask | null {
  try {
    const raw = localStorage.getItem(PENDING_TASK_STORAGE_KEY)
    if (!raw) return null
    const obj = JSON.parse(raw)
    if (
      obj &&
      typeof obj.task_id === 'string' &&
      typeof obj.project_name === 'string' &&
      typeof obj.started_at === 'number'
    ) {
      return obj as PendingTask
    }
    return null
  } catch {
    return null
  }
}

export { persistPendingTask, loadPendingTask }
export type { PendingTask }

function loadNavWidth(): number {
  try {
    const v = Number(localStorage.getItem(NAV_WIDTH_STORAGE_KEY))
    if (Number.isFinite(v) && v >= NAV_WIDTH_MIN && v <= NAV_WIDTH_MAX) return v
  } catch {
    // ignore
  }
  return NAV_WIDTH_DEFAULT
}

interface WikiStore {
  projectName: string | null
  projectFiles: Record<string, string>

  generateTaskId: string | null
  generateStatus: 'idle' | WikiTaskStatus
  generateMessage: string | null
  generateEvents: WikiProgressEvent[]
  lastGenerationDurationMs: number | null

  wiki: WikiDocument | null
  currentPageId: string | null

  rehydrating: boolean

  codeDrawer: CodeDrawerState
  drawerHeightRatio: number
  navWidthPx: number

  setProject: (name: string, files: Record<string, string>) => void
  setGenerateTaskId: (id: string | null) => void
  setGenerateStatus: (status: 'idle' | WikiTaskStatus, message?: string | null) => void
  setGenerateEvents: (events: WikiProgressEvent[]) => void
  setLastGenerationDurationMs: (ms: number | null) => void
  setWiki: (doc: WikiDocument) => void

  rehydrateFromStorage: () => Promise<void>

  navigateToPage: (pageId: string) => void
  openCodeDrawer: (refId: string) => void
  openCodeDrawerWithRef: (ref: CodeRef) => void
  closeCodeDrawer: () => void
  setDrawerHeightRatio: (r: number) => void
  setNavWidthPx: (px: number) => void
  reset: () => void
}

export const useWikiStore = create<WikiStore>((set, get) => ({
  projectName: null,
  projectFiles: {},

  generateTaskId: null,
  generateStatus: 'idle',
  generateMessage: null,
  generateEvents: [],
  lastGenerationDurationMs: null,

  wiki: null,
  currentPageId: null,

  rehydrating: false,

  codeDrawer: { open: false, ref: null },
  drawerHeightRatio: 0.4,
  navWidthPx: loadNavWidth(),

  setProject: (name, files) => {
    persistProjectName(name)
    set({ projectName: name, projectFiles: files })
  },

  rehydrateFromStorage: async () => {
    // 进行中任务优先：如果有未完成的 wiki 生成任务，不要去拉 wiki 文档
    // （文档还不存在），让 UploadView 拿到 pendingTask 自行恢复轮询即可。
    const pending = loadPendingTask()
    if (pending) {
      console.info('[rehydrateFromStorage] 检测到未完成任务，跳过 wiki 拉取:', pending)
      set({ projectName: pending.project_name })
      return
    }

    const name = loadPersistedProjectName()
    if (!name) return
    set({ rehydrating: true, projectName: name })
    try {
      // 并发拉 wiki 文档和项目源码：wiki 必须有，源码缺失只关闭 drawer 不致命
      const [doc, projectRes] = await Promise.all([
        getWikiDocument(name),
        getPersistedProject(name).catch((e) => {
          console.warn('[rehydrateFromStorage] 项目源码加载失败，drawer 将不可用:', e)
          return null
        }),
      ])
      const filesMap: Record<string, string> = {}
      if (projectRes) {
        for (const f of projectRes.files) filesMap[f.path] = f.content
      }
      set({
        wiki: doc,
        currentPageId: doc.index.root,
        projectFiles: filesMap,
      })
    } catch (e) {
      console.warn('[rehydrateFromStorage] 加载已保存项目失败，回退到上传页:', e)
      // 防御：rehydrate 期间用户可能已经开始新上传，不要清掉新的 projectName
      const currentPending = loadPendingTask()
      if (!currentPending) {
        persistProjectName(null)
        set({ projectName: null })
      }
    } finally {
      set({ rehydrating: false })
    }
  },

  setGenerateTaskId: (id) => set({ generateTaskId: id }),
  setGenerateStatus: (status, message = null) =>
    set({ generateStatus: status, generateMessage: message }),
  setGenerateEvents: (events) => set({ generateEvents: events }),
  setLastGenerationDurationMs: (ms) => set({ lastGenerationDurationMs: ms }),

  setWiki: (doc) =>
    set({
      wiki: doc,
      currentPageId: doc.index.root,
    }),

  navigateToPage: (pageId) => {
    const { wiki } = get()
    if (!wiki) return
    const page = wiki.pages.find((p) => p.id === pageId)
    if (!page) return
    // 分类节点不可导航
    if (page.type === 'category') return
    // 委托给 layout store：在 active leaf 中打开/激活对应 wiki tab
    useLayoutStore.getState().openWikiPage(pageId)
    // 同步 currentPageId（NavTree 高亮 / openCodeDrawer / Quiz 上下文等仍依赖此值）
    set({ currentPageId: pageId, codeDrawer: { open: false, ref: null } })
  },

  openCodeDrawer: (refId) => {
    const { wiki, currentPageId } = get()
    if (!wiki || !currentPageId) return
    const page = wiki.pages.find((p) => p.id === currentPageId)
    if (!page) return
    const ref = page.metadata.code_refs[refId]
    if (!ref) return
    set({ codeDrawer: { open: true, ref } })
  },

  openCodeDrawerWithRef: (ref) => set({ codeDrawer: { open: true, ref } }),

  closeCodeDrawer: () => set({ codeDrawer: { open: false, ref: null } }),

  setDrawerHeightRatio: (r) =>
    set({ drawerHeightRatio: Math.max(0.2, Math.min(0.75, r)) }),

  setNavWidthPx: (px) => {
    const clamped = Math.max(NAV_WIDTH_MIN, Math.min(NAV_WIDTH_MAX, px))
    try {
      localStorage.setItem(NAV_WIDTH_STORAGE_KEY, String(clamped))
    } catch {
      // ignore
    }
    set({ navWidthPx: clamped })
  },

  reset: () => {
    persistProjectName(null)
    set({
      projectName: null,
      projectFiles: {},
      generateTaskId: null,
      generateStatus: 'idle',
      generateMessage: null,
      generateEvents: [],
      lastGenerationDurationMs: null,
      wiki: null,
      currentPageId: null,
      codeDrawer: { open: false, ref: null },
    })
  },
}))

export type { WikiPage }
