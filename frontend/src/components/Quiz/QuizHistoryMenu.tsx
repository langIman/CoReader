import { useEffect } from 'react'
import { useQuizStore } from '../../store/useQuizStore'
import { useWikiStore } from '../../store/useWikiStore'
import type { QuizMode, QuizSession } from '../../types/quiz'

const MODE_LABEL: Record<QuizMode, string> = {
  history: '问答历史',
  page: '页面专项',
  project: '全项目',
}

function formatDate(iso: string): string {
  try {
    const d = new Date(iso)
    return `${d.getMonth() + 1}-${d.getDate()} ${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
  } catch {
    return iso
  }
}

export default function QuizHistoryMenu() {
  const projectName = useWikiStore((s) => s.projectName)
  const sessions = useQuizStore((s) => s.sessions)
  const sessionsLoaded = useQuizStore((s) => s.sessionsLoaded)
  const showHistory = useQuizStore((s) => s.showHistory)
  const loadSessions = useQuizStore((s) => s.loadSessions)
  const selectSession = useQuizStore((s) => s.selectSession)
  const removeSession = useQuizStore((s) => s.removeSession)
  const toggleHistory = useQuizStore((s) => s.toggleHistory)

  useEffect(() => {
    if (showHistory && projectName && !sessionsLoaded) {
      void loadSessions(projectName)
    }
  }, [showHistory, projectName, sessionsLoaded, loadSessions])

  if (!showHistory) return null

  return (
    <div className="absolute inset-0 z-10 bg-white dark:bg-gray-900 flex flex-col">
      <div className="flex items-center justify-between px-4 py-2 border-b border-gray-200 dark:border-gray-700">
        <div className="text-sm font-medium text-gray-700 dark:text-gray-200">
          📚 历史测验
        </div>
        <button
          onClick={toggleHistory}
          className="text-xs text-gray-500 dark:text-gray-400 hover:text-gray-700 dark:hover:text-gray-200"
        >
          ✕ 关闭
        </button>
      </div>

      <div className="flex-1 overflow-auto p-3">
        {!sessionsLoaded ? (
          <div className="text-center text-xs text-gray-400 mt-10">加载中...</div>
        ) : sessions.length === 0 ? (
          <div className="text-center text-xs text-gray-400 mt-10">
            暂无历史测验
          </div>
        ) : (
          <div className="space-y-2">
            {sessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                onSelect={() => void selectSession(s.id)}
                onDelete={() => projectName && void removeSession(s.id, projectName)}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function SessionItem({
  session,
  onSelect,
  onDelete,
}: {
  session: QuizSession
  onSelect: () => void
  onDelete: () => void
}) {
  const completed = session.answered_count >= 10
  const pct = completed ? Math.round((session.score / 10) * 100) : null

  return (
    <div className="group relative rounded-lg border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 p-3 hover:border-blue-400 dark:hover:border-blue-500 transition-colors">
      <button onClick={onSelect} className="w-full text-left">
        <div className="flex items-start justify-between gap-2 mb-1">
          <div className="text-sm font-medium text-gray-800 dark:text-gray-100 line-clamp-1">
            {session.title}
          </div>
        </div>
        <div className="flex items-center gap-2 text-[11px] text-gray-500 dark:text-gray-400">
          <span className="px-1.5 py-0.5 rounded bg-gray-100 dark:bg-gray-700 text-gray-600 dark:text-gray-300">
            {MODE_LABEL[session.mode]}
          </span>
          <span>{formatDate(session.created_at)}</span>
          {completed ? (
            <span
              className={`ml-auto font-medium ${
                pct! >= 70
                  ? 'text-green-600 dark:text-green-400'
                  : pct! >= 50
                    ? 'text-amber-600 dark:text-amber-400'
                    : 'text-red-600 dark:text-red-400'
              }`}
            >
              {session.score}/10 · {pct}%
            </span>
          ) : (
            <span className="ml-auto text-gray-400">
              {session.answered_count > 0
                ? `已答 ${session.answered_count}/10`
                : '未开始'}
            </span>
          )}
        </div>
      </button>
      <button
        onClick={(e) => {
          e.stopPropagation()
          if (confirm('确定删除该测验？')) onDelete()
        }}
        className="absolute top-2 right-2 opacity-0 group-hover:opacity-100 text-xs text-gray-400 hover:text-red-500 transition-opacity"
        title="删除"
      >
        🗑
      </button>
    </div>
  )
}
