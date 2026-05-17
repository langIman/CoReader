import { useEffect, useRef, useState } from 'react'
import { useWikiStore } from '../../store/useWikiStore'
import { useQAStore } from '../../store/useQAStore'
import { useQuizStore } from '../../store/useQuizStore'
import {
  findActiveWikiPageId,
  useLayoutStore,
} from '../../store/useLayoutStore'
import { downloadWikiMarkdown } from '../../services/api'
import FloatingCodeWindow from '../CodeDrawer/FloatingCodeWindow'
import LayoutTree from '../Layout/LayoutTree'
import QAHandle from '../QA/QAHandle'
import Resizer from '../common/Resizer'
import ThemeToggle from '../common/ThemeToggle'
import NavTree from './NavTree'

function formatDuration(ms: number): string {
  const total = Math.max(0, Math.round(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  if (m === 0) return `${s}s`
  return `${m}m ${String(s).padStart(2, '0')}s`
}

export default function WikiLayout() {
  const projectName = useWikiStore((s) => s.projectName)
  const lastGenerationDurationMs = useWikiStore((s) => s.lastGenerationDurationMs)
  const wiki = useWikiStore((s) => s.wiki)
  const currentPageId = useWikiStore((s) => s.currentPageId)
  const navWidthPx = useWikiStore((s) => s.navWidthPx)
  const setNavWidthPx = useWikiStore((s) => s.setNavWidthPx)
  const reset = useWikiStore((s) => s.reset)

  // QA / Quiz：仅用于触发 reset
  const resetQA = useQAStore((s) => s.reset)
  const resetQuiz = useQuizStore((s) => s.reset)
  const openQuizForPage = useQuizStore((s) => s.openForPage)
  const openQuizGlobal = useQuizStore((s) => s.openForGlobal)

  // 布局树（撑满 nav 之外的全部空间）
  const layoutRoot = useLayoutStore((s) => s.root)
  const setupDefault = useLayoutStore((s) => s.setupDefault)

  const [exporting, setExporting] = useState(false)

  // 每次项目切换（projectName 变化）时重置为默认布局：左 wiki + 右问答横向分屏。
  // 这确保页面刷新或重新打开旧项目时 QA 始终在右侧。
  const prevProjectRef = useRef<string | null>(null)
  useEffect(() => {
    if (!wiki?.index?.root || !projectName) return
    if (prevProjectRef.current !== projectName) {
      setupDefault(wiki.index.root)
      prevProjectRef.current = projectName
    }
  }, [wiki, projectName, setupDefault])

  // 派生 currentPageId：用户切 wiki tab 时，把当前 active wiki page id 同步给 wikiStore
  // （NavTree 高亮 / openCodeDrawer 查 ref / Quiz 上下文等仍依赖此值）
  useEffect(() => {
    const activeWikiPageId = findActiveWikiPageId(layoutRoot)
    if (!activeWikiPageId) return
    if (activeWikiPageId === currentPageId) return
    useWikiStore.setState({ currentPageId: activeWikiPageId })
  }, [layoutRoot, currentPageId])

  const handleReset = () => {
    resetQA()
    resetQuiz()
    reset()
  }

  // 头部"测验"按钮：若当前有焦点页面则预选 page 模式，否则退化为 project 模式
  const handleOpenQuiz = () => {
    const page = currentPageId
      ? wiki?.pages.find((p) => p.id === currentPageId && p.type !== 'category')
      : null
    if (page) {
      openQuizForPage(page.id, page.title)
    } else {
      openQuizGlobal()
    }
  }

  const handleExport = async () => {
    if (!projectName || exporting) return
    try {
      setExporting(true)
      await downloadWikiMarkdown(projectName)
    } catch (e) {
      console.error('[export] 导出失败:', e)
      alert(e instanceof Error ? e.message : '导出失败')
    } finally {
      setExporting(false)
    }
  }

  return (
    <div className="flex flex-col h-screen bg-white dark:bg-gray-900">
      <header className="flex items-center px-4 py-2 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 gap-3 flex-shrink-0">
        <div className="flex items-center gap-2">
          <span className="text-base">📖</span>
          <span className="font-semibold text-gray-800 dark:text-gray-100">CoReader</span>
          {projectName && (
            <>
              <span className="text-gray-300 dark:text-gray-600">/</span>
              <code className="font-mono text-sm text-gray-600 dark:text-gray-400">
                {projectName}
              </code>
            </>
          )}
          {lastGenerationDurationMs !== null && (
            <span
              className="ml-1 inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-emerald-50 dark:bg-emerald-900/30 text-emerald-700 dark:text-emerald-300 border border-emerald-200 dark:border-emerald-800"
              title="本次生成耗时"
            >
              ⏱ 生成耗时 {formatDuration(lastGenerationDurationMs)}
            </span>
          )}
        </div>

        <div className="h-5 w-px bg-gray-200 dark:bg-gray-700 mx-1" />

        <nav className="flex items-center gap-1">
          <button
            className="px-3 py-1 text-sm rounded bg-blue-50 dark:bg-blue-900/30 text-blue-700 dark:text-blue-300 font-medium"
            aria-current="page"
          >
            📖 Wiki
          </button>
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={handleOpenQuiz}
            disabled={!projectName}
            className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="基于当前页面或全项目生成 10 道测验题"
          >
            📝 测验
          </button>
          <button
            onClick={() => void handleExport()}
            disabled={!projectName || exporting}
            className="text-xs px-2 py-1 rounded border border-gray-300 dark:border-gray-600 text-gray-600 dark:text-gray-300 hover:bg-gray-100 dark:hover:bg-gray-700 disabled:opacity-50 disabled:cursor-not-allowed"
            title="将整份 Wiki 导出为单个 Markdown 文件"
          >
            {exporting ? '导出中...' : '📥 导出 Markdown'}
          </button>
          <button
            onClick={handleReset}
            className="text-xs text-gray-500 dark:text-gray-400 hover:text-red-500 dark:hover:text-red-400"
            title="返回上传视图"
          >
            重新上传
          </button>
          <div className="h-4 w-px bg-gray-200 dark:bg-gray-700" />
          <ThemeToggle />
        </div>
      </header>

      <div className="flex flex-1 overflow-hidden">
        {/* 左：导航 */}
        <aside
          className="flex-shrink-0 overflow-hidden"
          style={{ width: `${navWidthPx}px` }}
        >
          <NavTree />
        </aside>
        <Resizer
          onDrag={(dx) => setNavWidthPx(navWidthPx + dx)}
          title="拖动调整导航宽度"
        />

        {/* 右：layout tree 占满剩余空间。空树时显示引导。 */}
        <div className="flex-1 flex min-w-0 bg-white dark:bg-gray-900">
          {layoutRoot ? (
            <LayoutTree node={layoutRoot} />
          ) : (
            <div className="flex-1 flex items-center justify-center text-sm text-gray-400 dark:text-gray-500">
              请从左侧导航选择页面，或点击右上角"问答 / 测验"按钮
            </div>
          )}
        </div>
      </div>

      <QAHandle />
      <FloatingCodeWindow />
    </div>
  )
}
