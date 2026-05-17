import type { QuizOption } from '../../types/quiz'

interface Props {
  option: QuizOption
  revealed: boolean
  isCorrect: boolean
  isChosen: boolean
  onClick: () => void
}

export default function OptionItem({
  option,
  revealed,
  isCorrect,
  isChosen,
  onClick,
}: Props) {
  // 视觉状态
  let containerCls =
    'w-full text-left rounded-lg border p-3 transition-all '
  if (!revealed) {
    containerCls
      += 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 hover:border-blue-400 dark:hover:border-blue-500 cursor-pointer'
  } else if (isCorrect) {
    containerCls
      += 'border-green-500 bg-green-50 dark:bg-green-900/20 ring-1 ring-green-400'
  } else if (isChosen) {
    containerCls
      += 'border-red-500 bg-red-50 dark:bg-red-900/20 ring-1 ring-red-400'
  } else {
    containerCls
      += 'border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800 opacity-60'
  }

  return (
    <button
      type="button"
      onClick={onClick}
      disabled={revealed}
      className={containerCls}
    >
      <div className="flex items-start gap-3">
        <span className="flex-shrink-0 inline-flex items-center justify-center w-6 h-6 rounded-full text-xs font-bold border border-gray-300 dark:border-gray-600 text-gray-700 dark:text-gray-200">
          {option.key}
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm text-gray-800 dark:text-gray-100">
            {option.text}
          </div>
          {revealed && (
            <div className="mt-2 pt-2 border-t border-gray-200 dark:border-gray-700">
              <div className="flex items-center gap-1.5 mb-1">
                {isCorrect ? (
                  <span className="text-xs font-medium text-green-700 dark:text-green-400">
                    ✓ {isChosen ? '回答正确！' : '正确答案'}
                  </span>
                ) : isChosen ? (
                  <span className="text-xs font-medium text-red-700 dark:text-red-400">
                    ✗ 不正确
                  </span>
                ) : (
                  <span className="text-xs font-medium text-gray-500 dark:text-gray-400">
                    解析
                  </span>
                )}
              </div>
              <div className="text-xs text-gray-600 dark:text-gray-300 leading-relaxed">
                {option.explanation}
              </div>
            </div>
          )}
        </div>
      </div>
    </button>
  )
}
