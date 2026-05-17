import { useQuizStore } from '../../store/useQuizStore'

export default function GeneratingProgress() {
  const thinkingStatus = useQuizStore((s) => s.thinkingStatus)
  const generatedCount = useQuizStore((s) => s.generatedCount)
  const cancelGenerate = useQuizStore((s) => s.cancelGenerate)
  const resetToModeSelect = useQuizStore((s) => s.resetToModeSelect)

  const total = 10
  const phase = generatedCount === 0 ? 'thinking' : 'generating'
  const pct = (generatedCount / total) * 100

  return (
    <div className="flex-1 flex flex-col items-center justify-center px-6 py-10">
      <div className="w-full max-w-sm">
        <div className="text-center mb-6">
          <div className="text-4xl mb-3">{phase === 'thinking' ? '🔍' : '✍️'}</div>
          <h3 className="text-base font-medium text-gray-800 dark:text-gray-100 mb-1">
            {phase === 'thinking' ? 'Agent 正在分析代码库' : '题目生成中'}
          </h3>
          <p className="text-xs text-gray-500 dark:text-gray-400">
            {phase === 'thinking'
              ? thinkingStatus || '思考中...'
              : `已生成 ${generatedCount} / ${total} 题`}
          </p>
        </div>

        <div className="h-2 bg-gray-100 dark:bg-gray-800 rounded-full overflow-hidden mb-4">
          {phase === 'thinking' ? (
            <div className="h-full bg-blue-400 dark:bg-blue-500 animate-pulse w-1/3" />
          ) : (
            <div
              className="h-full bg-blue-500 dark:bg-blue-400 transition-all duration-300"
              style={{ width: `${pct}%` }}
            />
          )}
        </div>

        {phase === 'thinking' && (
          <div className="text-center text-[11px] text-gray-400 dark:text-gray-500 mb-6">
            <div className="inline-flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 bg-blue-400 rounded-full animate-pulse" />
              {thinkingStatus || '正在调用工具...'}
            </div>
          </div>
        )}

        <div className="flex justify-center gap-2">
          <button
            onClick={() => {
              cancelGenerate()
              resetToModeSelect()
            }}
            className="text-xs text-gray-500 dark:text-gray-400 hover:text-red-500 dark:hover:text-red-400"
          >
            取消生成
          </button>
        </div>
      </div>
    </div>
  )
}
