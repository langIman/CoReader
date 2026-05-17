import QADrawer from '../QA/QADrawer'
import QuizDrawer from '../Quiz/QuizDrawer'
import WikiPageView from '../Wiki/WikiPageView'
import type { LeafNode, TabId } from '../../store/useLayoutStore'
import { getWikiPageId, useLayoutStore } from '../../store/useLayoutStore'
import DropZones from './DropZones'
import TabHeader from './TabHeader'

interface Props {
  leaf: LeafNode
}

/**
 * 单个 group：tab bar + 当前激活 tab 的内容。
 *
 * M1：QA / Quiz 内容根据 activeTab 切换渲染（mount/unmount）。
 * Drawer 自身的状态都在 zustand store 里，重新 mount 不会丢内容。
 */
export default function TabGroup({ leaf }: Props) {
  const setActiveTab = useLayoutStore((s) => s.setActiveTab)
  const closeTab = useLayoutStore((s) => s.closeTab)

  const renderContent = (tab: TabId) => {
    if (tab === 'qa') return <QADrawer />
    if (tab === 'quiz') return <QuizDrawer />
    const pageId = getWikiPageId(tab)
    if (pageId) return <WikiPageView pageId={pageId} />
    return null
  }

  return (
    <div className="flex flex-col h-full w-full min-w-0 bg-white dark:bg-gray-900 relative">
      {/* Tab bar */}
      <div
        role="tablist"
        className="flex items-stretch border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex-shrink-0"
      >
        {leaf.tabs.map((tab) => (
          <TabHeader
            key={tab}
            tabId={tab}
            active={leaf.activeTab === tab}
            onClick={() => setActiveTab(leaf.id, tab)}
            onClose={() => closeTab(tab)}
          />
        ))}
      </div>

      {/* Content area: 仅渲染激活 tab */}
      <div className="flex-1 min-h-0 overflow-hidden">
        {leaf.activeTab && renderContent(leaf.activeTab)}
      </div>

      {/* 拖拽时叠加 5 区 drop zones（仅 isDragging 时可见可点） */}
      <DropZones groupId={leaf.id} groupTabs={leaf.tabs} />
    </div>
  )
}
