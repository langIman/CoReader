import { useLayoutStore } from '../../store/useLayoutStore'

export default function QAHandle() {
  const hasQA = useLayoutStore((s) => s.hasTab('qa'))
  const openTab = useLayoutStore((s) => s.openTab)
  const closeTab = useLayoutStore((s) => s.closeTab)

  // 按钮贴屏幕右边沿；layout tree 撑满剩余宽度，QA tab 在哪个 group 由用户决定
  const rightStyle = '0px'

  const onClick = () => {
    if (hasQA) closeTab('qa')
    else openTab('qa')
  }

  return (
    <button
      onClick={onClick}
      aria-label={hasQA ? '关闭问答面板' : '打开问答面板'}
      title={hasQA ? '关闭问答' : '打开问答'}
      className="group fixed top-1/2 -translate-y-1/2 z-30 h-14 w-4 flex flex-col items-center justify-center gap-1
                 bg-white/80 dark:bg-gray-800/80 backdrop-blur-sm
                 hover:bg-gray-50 dark:hover:bg-gray-700
                 text-gray-400 hover:text-gray-600 dark:text-gray-500 dark:hover:text-gray-300
                 shadow-sm hover:shadow
                 rounded-l-md border border-r-0 border-gray-200 dark:border-gray-700
                 transition-[right,background-color,color] duration-200"
      style={{ right: rightStyle }}
    >
      <svg
        width="10"
        height="10"
        viewBox="0 0 10 10"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {hasQA ? (
          <path d="M3.5 2 L6.5 5 L3.5 8" />
        ) : (
          <path d="M6.5 2 L3.5 5 L6.5 8" />
        )}
      </svg>
      {!hasQA && (
        <span
          className="text-[9px] leading-none writing-vertical text-gray-400 group-hover:text-gray-600 dark:text-gray-500 dark:group-hover:text-gray-300"
          style={{ writingMode: 'vertical-rl' }}
        >
          问答
        </span>
      )}
    </button>
  )
}
