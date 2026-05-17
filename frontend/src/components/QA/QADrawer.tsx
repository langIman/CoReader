import { useQAStore } from '../../store/useQAStore'
import ConversationMenu from './ConversationMenu'
import InputBox from './InputBox'
import MessageList from './MessageList'

/**
 * QA 抽屉的内容。外层 aside + Resizer + Tab 头由 LayoutTree/TabGroup 编排。
 * 关闭逻辑由 TabHeader 上的 ✕ 触发 layoutStore.closeTab('qa')。
 */
export default function QADrawer() {
  const newConversation = useQAStore((s) => s.newConversation)

  return (
    <div className="flex flex-col h-full min-w-0">
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-800 flex-shrink-0">
        <ConversationMenu />
        <button
          onClick={newConversation}
          className="text-xs text-gray-500 dark:text-gray-400 hover:text-blue-600 dark:hover:text-blue-400 px-2 py-1 rounded"
          title="新建对话"
        >
          + 新建
        </button>
      </div>
      <MessageList />
      <InputBox />
    </div>
  )
}
