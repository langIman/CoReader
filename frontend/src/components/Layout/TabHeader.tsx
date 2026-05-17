import type { TabId } from '../../store/useLayoutStore'
import { getWikiPageId, useLayoutStore } from '../../store/useLayoutStore'
import { useWikiStore } from '../../store/useWikiStore'

/** dataTransfer 里携带 tabId 的 mime type，统一识别 */
export const TAB_DRAG_MIME = 'application/x-coreader-tab'

interface Props {
  tabId: TabId
  active: boolean
  onClick: () => void
  onClose: () => void
}

export default function TabHeader({ tabId, active, onClick, onClose }: Props) {
  const wiki = useWikiStore((s) => s.wiki)
  const setDraggingTab = useLayoutStore((s) => s.setDraggingTab)

  // 动态计算 tab 标题：QA / Quiz 是固定文案，wiki tab 从 page.title 取
  let label: string
  let icon: string
  const wikiPageId = getWikiPageId(tabId)
  if (tabId === 'qa') {
    label = '问答'
    icon = '💬'
  } else if (tabId === 'quiz') {
    label = '测验'
    icon = '📝'
  } else if (wikiPageId) {
    const page = wiki?.pages.find((p) => p.id === wikiPageId)
    label = page?.title ?? wikiPageId.slice(0, 8)
    icon = '📄'
  } else {
    label = String(tabId)
    icon = '?'
  }

  const onDragStart = (e: React.DragEvent) => {
    e.dataTransfer.effectAllowed = 'move'
    e.dataTransfer.setData(TAB_DRAG_MIME, tabId)
    // 部分浏览器需要也设置 'text/plain' 才能触发 drag
    e.dataTransfer.setData('text/plain', tabId)
    // 显式用当前 tab 作为 drag image，避免后续 rerender 让浏览器找不到 source
    e.dataTransfer.setDragImage(e.currentTarget as HTMLElement, 12, 12)
    // 关键：延迟 setState 一帧，让浏览器先完成 drag 启动，避免 React rerender 中断 drag operation
    requestAnimationFrame(() => setDraggingTab(tabId))
  }

  const onDragEnd = () => {
    setDraggingTab(null)
  }

  return (
    <div
      role="tab"
      aria-selected={active}
      onClick={onClick}
      draggable
      onDragStart={onDragStart}
      onDragEnd={onDragEnd}
      className={`group flex items-center gap-1.5 px-3 py-1.5 text-xs cursor-pointer select-none border-r border-gray-200 dark:border-gray-700 ${
        active
          ? 'bg-white dark:bg-gray-900 text-gray-800 dark:text-gray-100 border-b-2 border-b-blue-500'
          : 'bg-gray-100 dark:bg-gray-800 text-gray-500 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-700/60'
      }`}
    >
      <span className="text-sm">{icon}</span>
      <span className="font-medium max-w-[180px] truncate">{label}</span>
      <button
        type="button"
        onClick={(e) => {
          e.stopPropagation()
          onClose()
        }}
        aria-label={`关闭${label}`}
        className="ml-1 w-4 h-4 flex items-center justify-center rounded text-gray-400 hover:text-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 dark:hover:text-gray-200 opacity-60 group-hover:opacity-100 transition-opacity"
      >
        <svg width="10" height="10" viewBox="0 0 10 10" fill="none" stroke="currentColor" strokeWidth="1.5">
          <path d="M2 2 L8 8 M8 2 L2 8" />
        </svg>
      </button>
    </div>
  )
}
