import { useQuizStore } from '../../store/useQuizStore'
import { useQAStore } from '../../store/useQAStore'
import { useLayoutStore } from '../../store/useLayoutStore'
import { useWikiStore } from '../../store/useWikiStore'
import OptionItem from './OptionItem'

export default function QuizCard() {
  const questions = useQuizStore((s) => s.questions)
  const currentIndex = useQuizStore((s) => s.currentIndex)
  const answers = useQuizStore((s) => s.answers)
  const revealed = useQuizStore((s) => s.revealed)
  const goNext = useQuizStore((s) => s.goNext)
  const selectAnswer = useQuizStore((s) => s.selectAnswer)
  const sessionTitle = useQuizStore((s) => s.sessionTitle)
  const resetToModeSelect = useQuizStore((s) => s.resetToModeSelect)

  const projectName = useWikiStore((s) => s.projectName)
  const askQA = useQAStore((s) => s.ask)
  const openTab = useLayoutStore((s) => s.openTab)

  const question = questions[currentIndex]
  if (!question) return null

  const total = questions.length
  const isRevealed = !!revealed[question.index]
  const chosenKey = answers[question.index]
  const isLast = currentIndex >= total - 1

  const handleExplain = () => {
    if (!projectName || !chosenKey) return
    const chosenText = question.options.find((o) => o.key === chosenKey)?.text ?? ''
    const correctText = question.options.find((o) => o.key === question.correct_key)?.text ?? ''
    const isWrong = chosenKey !== question.correct_key
    const prompt = isWrong
      ? `关于测验第 ${question.index + 1} 题：\n\n> ${question.question_text}\n\n`
        + `我选了 ${chosenKey}（${chosenText}），但正确答案是 ${question.correct_key}（${correctText}）。`
        + `请结合代码详细解释正确答案为什么对、我选的为什么不对。`
      : `关于测验第 ${question.index + 1} 题：\n\n> ${question.question_text}\n\n`
        + `正确答案是 ${question.correct_key}（${correctText}）。请结合代码详细讲解相关知识点。`
    openTab('qa')
    void askQA(prompt, projectName)
  }

  return (
    <div className="flex-1 overflow-auto px-6 py-5">
      <div className="max-w-md mx-auto">
        {/* 顶部：标题 + 进度 */}
        <div className="flex items-baseline justify-between mb-1">
          <div className="text-xs text-gray-500 dark:text-gray-400 truncate" title={sessionTitle}>
            {sessionTitle}
          </div>
          <button
            onClick={resetToModeSelect}
            className="text-[11px] text-gray-400 hover:text-gray-600 dark:hover:text-gray-200"
            title="退出当前测验"
          >
            退出
          </button>
        </div>
        <div className="text-xs text-gray-500 dark:text-gray-400 mb-3">
          {currentIndex + 1} / {total}
        </div>

        {/* 题目 */}
        <h3 className="text-base font-medium text-gray-800 dark:text-gray-100 mb-4 leading-relaxed">
          {question.question_text}
        </h3>

        {/* 选项 */}
        <div className="space-y-2 mb-4">
          {question.options.map((opt) => (
            <OptionItem
              key={opt.key}
              option={opt}
              revealed={isRevealed}
              isCorrect={opt.key === question.correct_key}
              isChosen={chosenKey === opt.key}
              onClick={() => void selectAnswer(question.index, opt.key)}
            />
          ))}
        </div>

        {/* code_ref */}
        {isRevealed && question.code_ref && (
          <div className="mb-4 px-3 py-2 bg-gray-50 dark:bg-gray-800/60 border border-gray-200 dark:border-gray-700 rounded text-xs text-gray-600 dark:text-gray-400">
            <span className="text-gray-500 dark:text-gray-500">📍 代码依据：</span>
            <code className="font-mono">
              {question.code_ref.file}:{question.code_ref.line_start}-{question.code_ref.line_end}
            </code>
          </div>
        )}

        {/* 底部按钮 */}
        <div className="flex items-center justify-between">
          <button
            onClick={handleExplain}
            disabled={!isRevealed}
            className="text-xs px-3 py-1.5 rounded border border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:bg-gray-50 dark:hover:bg-gray-800 disabled:opacity-40 disabled:cursor-not-allowed"
          >
            💡 解释
          </button>
          <button
            onClick={goNext}
            disabled={!isRevealed}
            className="text-sm px-4 py-1.5 rounded-full bg-blue-500 hover:bg-blue-600 text-white font-medium disabled:opacity-40 disabled:cursor-not-allowed"
          >
            {isLast ? '查看结果' : '下一题'}
          </button>
        </div>
      </div>
    </div>
  )
}
