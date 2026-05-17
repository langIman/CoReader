import { useWikiStore } from '../../store/useWikiStore'
import { useQuizStore } from '../../store/useQuizStore'
import MarkdownRenderer from './MarkdownRenderer'
import type { WikiPage } from '../../types/wiki'

interface Props {
  /**
   * 该 view 显示的 page id。每个 wiki tab 在自己的 TabGroup 里渲染一个独立 WikiPageView，
   * 各自携带不同 pageId，互不干扰。
   */
  pageId: string
}

export default function WikiPageView({ pageId }: Props) {
  const wiki = useWikiStore((s) => s.wiki)
  const openQuizForPage = useQuizStore((s) => s.openForPage)

  if (!wiki) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
        Wiki 加载中…
      </div>
    )
  }

  const page = wiki.pages.find((p) => p.id === pageId)

  if (!page) {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
        页面不存在
      </div>
    )
  }

  // category 节点理论上不应被导航到，防御性返回
  if (page.type === 'category') {
    return (
      <div className="flex-1 flex items-center justify-center text-gray-400 text-sm">
        请从左侧选择具体页面
      </div>
    )
  }

  return (
    // h-full 而非 flex-1：父容器是 block 不是 flex，flex-1 在这里不生效，
    // 会导致没有明确高度从而 overflow-auto 失效（页面整个滚不动）。
    <div className="h-full overflow-auto">
      <div className="max-w-4xl mx-auto px-8 py-6">
        <header className="mb-4 pb-3 border-b border-gray-200 dark:border-gray-700">
          <div className="flex items-center justify-between gap-3 mb-1">
            <div className="flex items-center gap-2 text-xs text-gray-500 dark:text-gray-400 min-w-0">
              <span>{labelFor(page.type)}</span>
              {page.path && (
                <>
                  <span>·</span>
                  <code className="font-mono truncate">{page.path}</code>
                </>
              )}
            </div>
            <button
              onClick={() => openQuizForPage(page.id, page.title)}
              className="flex-shrink-0 inline-flex items-center gap-1 text-xs px-2.5 py-1 rounded-full border border-blue-200 dark:border-blue-800 text-blue-600 dark:text-blue-300 bg-blue-50 dark:bg-blue-900/20 hover:bg-blue-100 dark:hover:bg-blue-900/40 transition-colors"
              title="基于本页内容生成 10 道测验题"
            >
              📝 测验本页
            </button>
          </div>
          <h1 className="text-2xl font-bold text-gray-900 dark:text-gray-100">{page.title}</h1>
        </header>
        {page.content_md ? (
          <MarkdownRenderer content={page.content_md} />
        ) : (
          <p className="text-sm text-gray-400">该页面无内容</p>
        )}
      </div>
    </div>
  )
}

function labelFor(type: Exclude<WikiPage['type'], 'category'>): string {
  switch (type) {
    case 'overview':
      return '项目概览'
    case 'chapter':
      return '核心架构'
    case 'topic':
      return '专题深入'
    case 'module':
      return '模块'
  }
}
