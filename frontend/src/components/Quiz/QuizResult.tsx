import { useQuizStore } from '../../store/useQuizStore'

export default function QuizResult() {
  const questions = useQuizStore((s) => s.questions)
  const answers = useQuizStore((s) => s.answers)
  const sessionTitle = useQuizStore((s) => s.sessionTitle)
  const goTo = useQuizStore((s) => s.goTo)
  const resetToModeSelect = useQuizStore((s) => s.resetToModeSelect)

  const total = questions.length
  const correct = questions.filter((q) => answers[q.index] === q.correct_key).length
  const pct = total > 0 ? Math.round((correct / total) * 100) : 0

  const grade = pct >= 90 ? '优秀' : pct >= 70 ? '良好' : pct >= 50 ? '及格' : '需努力'
  const emoji = pct >= 90 ? '🏆' : pct >= 70 ? '🎉' : pct >= 50 ? '👍' : '💪'

  return (
    <div className="flex-1 overflow-auto px-6 py-6">
      <div className="max-w-md mx-auto">
        <div className="text-xs text-gray-500 dark:text-gray-400 truncate mb-2" title={sessionTitle}>
          {sessionTitle}
        </div>

        <div className="text-center py-6 mb-4 bg-gradient-to-br from-blue-50 to-purple-50 dark:from-blue-900/20 dark:to-purple-900/20 rounded-lg border border-blue-100 dark:border-blue-900/40">
          <div className="text-5xl mb-3">{emoji}</div>
          <div className="text-3xl font-bold text-gray-800 dark:text-gray-100 mb-1">
            {correct} / {total}
          </div>
          <div className="text-sm text-gray-600 dark:text-gray-300">
            正确率 {pct}% · {grade}
          </div>
        </div>

        <div className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">
          📋 答题回顾
        </div>
        <div className="space-y-1.5 mb-5">
          {questions.map((q) => {
            const chosen = answers[q.index]
            const ok = chosen === q.correct_key
            return (
              <button
                key={q.index}
                onClick={() => goTo(q.index)}
                className="w-full text-left px-3 py-2 rounded border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:bg-gray-50 dark:hover:bg-gray-700/50 transition-colors"
              >
                <div className="flex items-start gap-2">
                  <span
                    className={`flex-shrink-0 inline-flex items-center justify-center w-5 h-5 rounded-full text-[10px] font-bold ${
                      ok
                        ? 'bg-green-100 text-green-700 dark:bg-green-900/40 dark:text-green-300'
                        : 'bg-red-100 text-red-700 dark:bg-red-900/40 dark:text-red-300'
                    }`}
                  >
                    {ok ? '✓' : '✗'}
                  </span>
                  <div className="flex-1 min-w-0">
                    <div className="text-xs text-gray-400 dark:text-gray-500 mb-0.5">
                      第 {q.index + 1} 题
                    </div>
                    <div className="text-sm text-gray-700 dark:text-gray-200 line-clamp-2">
                      {q.question_text}
                    </div>
                    {!ok && (
                      <div className="text-[11px] text-gray-500 dark:text-gray-400 mt-1">
                        你选 {chosen} · 正确 {q.correct_key}
                      </div>
                    )}
                  </div>
                </div>
              </button>
            )
          })}
        </div>

        <button
          onClick={resetToModeSelect}
          className="w-full py-2 px-4 rounded-lg border border-gray-200 dark:border-gray-700 text-sm text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 font-medium"
        >
          再来一组
        </button>
      </div>
    </div>
  )
}
