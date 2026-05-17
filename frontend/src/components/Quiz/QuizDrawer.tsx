import { useQuizStore } from '../../store/useQuizStore'
import GeneratingProgress from './GeneratingProgress'
import ModeSelector from './ModeSelector'
import QuizCard from './QuizCard'
import QuizHistoryMenu from './QuizHistoryMenu'
import QuizResult from './QuizResult'

/**
 * Quiz 抽屉的内容。外层 aside + Resizer + Tab 头由 LayoutTree/TabGroup 编排。
 * 关闭逻辑由 TabHeader 上的 ✕ 触发 layoutStore.closeTab('quiz')。
 */
export default function QuizDrawer() {
  const phase = useQuizStore((s) => s.phase)
  const showHistory = useQuizStore((s) => s.showHistory)
  const toggleHistory = useQuizStore((s) => s.toggleHistory)

  return (
    <div className="flex flex-col h-full min-w-0 relative">
      {/* 工具栏（去掉了主标题，由 TabBar 显示；保留历史按钮） */}
      {phase !== 'generating' && (
        <div className="flex items-center justify-end px-3 py-1.5 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex-shrink-0">
          <button
            onClick={toggleHistory}
            className="px-2 py-1 text-xs text-gray-500 dark:text-gray-400 hover:bg-gray-100 dark:hover:bg-gray-700 rounded"
            title="历史测验"
          >
            📚 历史
          </button>
        </div>
      )}

      {/* Body */}
      <div className="flex-1 flex flex-col min-h-0 relative">
        {phase === 'mode_select' && <ModeSelector />}
        {phase === 'generating' && <GeneratingProgress />}
        {phase === 'quizzing' && <QuizCard />}
        {phase === 'result' && <QuizResult />}

        {/* 历史侧栏（覆盖在 body 上） */}
        {showHistory && <QuizHistoryMenu />}
      </div>
    </div>
  )
}
