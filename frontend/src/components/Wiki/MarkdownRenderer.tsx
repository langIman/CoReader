import { useContext, useEffect, useRef } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import hljs from 'highlight.js/lib/core'
import python from 'highlight.js/lib/languages/python'
import 'highlight.js/styles/github.css'
import mermaid from 'mermaid'
import { useWikiStore } from '../../store/useWikiStore'
import { QACodeRefsContext } from '../QA/QACodeRefsContext'

hljs.registerLanguage('python', python)
mermaid.initialize({ startOnLoad: false, theme: 'dark' })

interface Props {
  content: string
}

const PAGE_ID_RE = /^(?:overview|cat_(?:architecture|modules|topics)|module_\d+|chapter_\d+|topic_\d+)$/

function stripLeadingNumber(t: string): string {
  return t.replace(/^\s*\d+\s*[.、:：)）]\s*/, '').trim()
}

function MermaidBlock({ code }: { code: string }) {
  const ref = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (!ref.current) return
    const id = `mermaid-${Math.random().toString(36).slice(2)}`
    mermaid.render(id, code).then(({ svg }) => {
      if (ref.current) ref.current.innerHTML = svg
    }).catch(() => {
      if (ref.current) ref.current.textContent = code
    })
  }, [code])
  return <div ref={ref} className="my-4 flex justify-center" />
}

// 跳过 content_md 开头的 H1（WikiPageView header 已渲染标题，避免重复）
function stripLeadingH1(md: string): string {
  return md.replace(/^\s*#\s+[^\n]*\n?/, '')
}

export default function MarkdownRenderer({ content }: Props) {
  const processedContent = stripLeadingH1(content)
  const navigateToPage = useWikiStore((s) => s.navigateToPage)
  const openCodeDrawer = useWikiStore((s) => s.openCodeDrawer)
  const openCodeDrawerWithRef = useWikiStore((s) => s.openCodeDrawerWithRef)
  const wiki = useWikiStore((s) => s.wiki)
  const qaRefs = useContext(QACodeRefsContext)

  return (
    <div className="markdown-body text-gray-800 dark:text-gray-200 leading-7 text-[15px]">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a({ href, children }) {
            if (href?.startsWith('#wiki:')) {
              const pageId = href.slice(6)
              return (
                <a
                  href="#"
                  className="text-blue-600 dark:text-blue-400 hover:underline font-medium"
                  onClick={(e) => {
                    e.preventDefault()
                    void navigateToPage(pageId)
                  }}
                >
                  {children}
                </a>
              )
            }
            if (href?.startsWith('#code:')) {
              const refId = href.slice(6)
              return (
                <a
                  href="#"
                  className="text-purple-600 dark:text-purple-400 hover:underline font-mono text-[13px] px-1 py-0.5 bg-purple-50 dark:bg-purple-900/30 rounded"
                  onClick={(e) => {
                    e.preventDefault()
                    const qaRef = qaRefs?.[refId]
                    if (qaRef) openCodeDrawerWithRef(qaRef)
                    else openCodeDrawer(refId)
                  }}
                >
                  {children}
                </a>
              )
            }
            return (
              <a
                href={href}
                target="_blank"
                rel="noreferrer"
                className="text-blue-600 dark:text-blue-400 hover:underline"
              >
                {children}
              </a>
            )
          },
          h1({ children }) {
            return (
              <h1 className="text-2xl font-bold mt-4 mb-4 pb-2 border-b border-gray-200 dark:border-gray-700 text-gray-900 dark:text-gray-100">
                {children}
              </h1>
            )
          },
          h2({ children }) {
            return (
              <h2 className="text-xl font-semibold mt-6 mb-3 text-gray-900 dark:text-gray-100">
                {children}
              </h2>
            )
          },
          h3({ children }) {
            return (
              <h3 className="text-base font-semibold mt-5 mb-2 text-gray-900 dark:text-gray-100">
                {children}
              </h3>
            )
          },
          p({ children }) {
            return <p className="my-3">{children}</p>
          },
          ul({ children }) {
            return <ul className="list-disc pl-6 my-3 space-y-1">{children}</ul>
          },
          ol({ children }) {
            return <ol className="list-decimal pl-6 my-3 space-y-1">{children}</ol>
          },
          li({ children }) {
            return <li className="leading-7">{children}</li>
          },
          blockquote({ children }) {
            return (
              <blockquote className="border-l-4 border-gray-300 dark:border-gray-600 pl-4 my-3 text-gray-600 dark:text-gray-400 italic">
                {children}
              </blockquote>
            )
          },
          code({ className, children, ...rest }) {
            const match = /language-(\w+)/.exec(className || '')
            const raw = String(children)
            const isBlock = Boolean(match) || raw.includes('\n')
            if (!isBlock) {
              // LLM 在正文里偶尔会以 inline code 形式直接写出页面 id
              // (e.g. `module_4`, `chapter_2`)，把它替换成真实标题并接成可点链接。
              if (wiki && PAGE_ID_RE.test(raw.trim())) {
                const target = wiki.pages.find((p) => p.id === raw.trim())
                if (target && target.type !== 'category') {
                  const label =
                    target.type === 'chapter'
                      ? stripLeadingNumber(target.title)
                      : target.title
                  return (
                    <a
                      href="#"
                      className="text-blue-600 dark:text-blue-400 hover:underline font-medium"
                      onClick={(e) => {
                        e.preventDefault()
                        void navigateToPage(target.id)
                      }}
                    >
                      {label}
                    </a>
                  )
                }
              }
              return (
                <code
                  className="px-1.5 py-0.5 bg-gray-100 dark:bg-gray-800 rounded text-[13px] font-mono text-rose-600 dark:text-rose-300"
                  {...rest}
                >
                  {children}
                </code>
              )
            }
            const code = String(children).replace(/\n$/, '')
            if (match && match[1] === 'mermaid') {
              return <MermaidBlock code={code} />
            }
            let html = code
            if (match && match[1] === 'python') {
              try {
                html = hljs.highlight(code, { language: 'python' }).value
              } catch {
                // fallthrough
              }
            }
            return (
              <pre className="bg-gray-50 dark:bg-gray-800 rounded-lg p-4 overflow-x-auto my-4 text-[13px] leading-6 font-mono border border-gray-200 dark:border-gray-700">
                <code
                  dangerouslySetInnerHTML={{ __html: html === code ? escapeHtml(code) : html }}
                />
              </pre>
            )
          },
          table({ children }) {
            return (
              <div className="overflow-x-auto my-4">
                <table className="border-collapse border border-gray-300 dark:border-gray-600 text-sm">
                  {children}
                </table>
              </div>
            )
          },
          th({ children }) {
            return (
              <th className="border border-gray-300 dark:border-gray-600 px-3 py-1.5 bg-gray-50 dark:bg-gray-800 font-semibold text-left">
                {children}
              </th>
            )
          },
          td({ children }) {
            return (
              <td className="border border-gray-300 dark:border-gray-600 px-3 py-1.5">
                {children}
              </td>
            )
          },
          hr() {
            return <hr className="my-6 border-gray-200 dark:border-gray-700" />
          },
        }}
      >
        {processedContent}
      </ReactMarkdown>
    </div>
  )
}

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
}
