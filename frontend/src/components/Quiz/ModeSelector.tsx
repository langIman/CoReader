import { useEffect, useState } from 'react'
import { useQuizStore } from '../../store/useQuizStore'
import { useWikiStore } from '../../store/useWikiStore'
import type { QuizMode } from '../../types/quiz'

const MODE_META: Record<QuizMode, { label: string; description: string; icon: string }> = {
  history: {
    label: '问答历史',
    description: '基于你过往的 QA 提问，针对疑惑点设计测验',
    icon: '💬',
  },
  page: {
    label: '当前页面',
    description: '围绕指定 wiki 页面及其引用的代码考察理解',
    icon: '📄',
  },
  project: {
    label: '全项目随机',
    description: '在多个模块间分散考察，覆盖整体架构',
    icon: '🎲',
  },
}

export default function ModeSelector() {
  const projectName = useWikiStore((s) => s.projectName)
  const wiki = useWikiStore((s) => s.wiki)
  const currentPageId = useWikiStore((s) => s.currentPageId)
  const presetMode = useQuizStore((s) => s.presetMode)
  const presetSourceId = useQuizStore((s) => s.presetSourceId)
  const presetSourceTitle = useQuizStore((s) => s.presetSourceTitle)
  const setPresetMode = useQuizStore((s) => s.setPresetMode)
  const syncPageContext = useQuizStore((s) => s.syncPageContext)
  const startGenerate = useQuizStore((s) => s.startGenerate)
  const generateError = useQuizStore((s) => s.generateError)
  const toggleHistory = useQuizStore((s) => s.toggleHistory)

  // 抽屉打开期间用户切换 wiki 页面 → 同步预选 sourceId
  // 这样"📌 {页面标题}"会跟着变，page 模式始终指向当前焦点页面
  useEffect(() => {
    if (!currentPageId || !wiki) {
      syncPageContext(null, null)
      return
    }
    const page = wiki.pages.find(
      (p) => p.id === currentPageId && p.type !== 'category',
    )
    if (page) {
      syncPageContext(page.id, page.title)
    } else {
      syncPageContext(null, null)
    }
  }, [currentPageId, wiki, syncPageContext])

  // 历史模式可用性预检
  const [historyDisabled, setHistoryDisabled] = useState(false)
  useEffect(() => {
    if (!projectName) return
    let cancelled = false
    fetch(`/api/qa/conversations?project_name=${encodeURIComponent(projectName)}`)
      .then((r) => (r.ok ? r.json() : []))
      .then((list) => {
        if (cancelled) return
        setHistoryDisabled(!Array.isArray(list) || list.length === 0)
      })
      .catch(() => {
        if (cancelled) return
        setHistoryDisabled(true)
      })
    return () => {
      cancelled = true
    }
  }, [projectName])

  const handleStart = () => {
    if (!projectName) return
    if (presetMode === 'page' && !presetSourceId) return
    if (presetMode === 'history' && historyDisabled) return
    void startGenerate({
      project_name: projectName,
      mode: presetMode,
      source_id: presetMode === 'page' ? presetSourceId : null,
    })
  }

  const canStart =
    !!projectName
    && (presetMode !== 'history' || !historyDisabled)
    && (presetMode !== 'page' || !!presetSourceId)

  return (
    <div className="flex-1 overflow-auto p-6">
      <div className="max-w-md mx-auto">
        <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100 mb-1">
          📝 测验
        </h2>
        <p className="text-xs text-gray-500 dark:text-gray-400 mb-5">
          基于你的项目内容，自动生成 10 道选择题来检测理解程度
        </p>

        <div className="space-y-2 mb-4">
          {(['page', 'history', 'project'] as QuizMode[]).map((mode) => {
            const meta = MODE_META[mode]
            const disabled = mode === 'history' && historyDisabled
            const isPagedNoSource = mode === 'page' && !presetSourceId
            const finalDisabled = disabled || isPagedNoSource
            const active = presetMode === mode
            return (
              <button
                key={mode}
                disabled={finalDisabled}
                onClick={() => setPresetMode(mode)}
                className={`
                  w-full text-left p-3 rounded-lg border transition-all
                  ${active
                    ? 'border-blue-500 bg-blue-50 dark:bg-blue-900/20 ring-1 ring-blue-400'
                    : 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-gray-300 dark:hover:border-gray-600'}
                  ${finalDisabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'}
                `}
              >
                <div className="flex items-start gap-3">
                  <div className="text-xl flex-shrink-0">{meta.icon}</div>
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-gray-800 dark:text-gray-100">
                      {meta.label}
                    </div>
                    <div className="text-xs text-gray-500 dark:text-gray-400 mt-0.5">
                      {meta.description}
                    </div>
                    {mode === 'page' && (
                      <div className="text-xs mt-1.5">
                        {presetSourceId ? (
                          <span className="text-blue-600 dark:text-blue-400">
                            📌 {presetSourceTitle || presetSourceId}
                          </span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400">
                            请先选择一个 wiki 页面
                          </span>
                        )}
                      </div>
                    )}
                    {mode === 'history' && historyDisabled && (
                      <div className="text-xs mt-1.5 text-amber-600 dark:text-amber-400">
                        请先在问答区提问后再使用此模式
                      </div>
                    )}
                  </div>
                </div>
              </button>
            )
          })}
        </div>

        {generateError && (
          <div className="mb-3 p-3 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded text-sm text-red-700 dark:text-red-300">
            {generateError}
          </div>
        )}

        <div className="flex items-center gap-2">
          <button
            disabled={!canStart}
            onClick={handleStart}
            className="flex-1 py-2 px-4 rounded-lg bg-blue-500 hover:bg-blue-600 text-white text-sm font-medium disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            开始测验（10 题）
          </button>
          <button
            onClick={toggleHistory}
            className="px-3 py-2 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800"
            title="历史测验"
          >
            📚
          </button>
        </div>
      </div>
    </div>
  )
}
