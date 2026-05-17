import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error: Error | null
  info: ErrorInfo | null
}

/**
 * 顶层错误边界。
 *
 * 没有它时：组件树中任何渲染异常会让 React 把整棵树 unmount，
 * App.tsx 根据 store 当前状态重新挂载，常见结果就是用户莫名其妙
 * 被甩回上传初始页（wiki===null 时的默认渲染），错误信息丢失。
 *
 * 有它后：抛错被 captured，显示明确的错误面板 + 堆栈，并提供
 * 「继续」按钮让用户清空错误状态继续操作，或刷新页面冷启动。
 */
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, info: null }

  static getDerivedStateFromError(error: Error): Partial<State> {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    this.setState({ info })
    console.error('[ErrorBoundary] 捕获到渲染异常：', error, info)
  }

  private handleReset = () => {
    this.setState({ error: null, info: null })
  }

  private handleReload = () => {
    window.location.reload()
  }

  render() {
    const { error, info } = this.state
    if (!error) return this.props.children

    return (
      <div className="min-h-screen flex items-center justify-center p-6 bg-gray-50 dark:bg-gray-900">
        <div className="w-full max-w-2xl bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-red-200 dark:border-red-900 p-6">
          <div className="flex items-start gap-3 mb-4">
            <div className="text-3xl shrink-0">⚠️</div>
            <div>
              <h2 className="text-lg font-semibold text-gray-800 dark:text-gray-100">
                页面出现了一个错误
              </h2>
              <p className="text-sm text-gray-500 dark:text-gray-400 mt-1">
                错误已被拦截，UI 没有崩溃。可以「继续」清掉错误回到刚才的状态，
                如果反复出现请用「刷新页面」冷启动并把下方堆栈截给开发者。
              </p>
            </div>
          </div>

          <div className="rounded-lg bg-gray-50 dark:bg-gray-900/60 border border-gray-200 dark:border-gray-700 p-3 mb-4 max-h-72 overflow-auto">
            <p className="font-mono text-xs text-red-600 dark:text-red-300 break-words">
              {error.name}: {error.message}
            </p>
            {(error.stack || info?.componentStack) && (
              <pre className="mt-2 font-mono text-[11px] text-gray-600 dark:text-gray-400 whitespace-pre-wrap break-words">
{error.stack || ''}
{info?.componentStack ? `\n--- Component stack ---${info.componentStack}` : ''}
              </pre>
            )}
          </div>

          <div className="flex justify-end gap-2">
            <button
              onClick={this.handleReset}
              className="px-4 py-1.5 text-sm text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
            >
              继续
            </button>
            <button
              onClick={this.handleReload}
              className="px-4 py-1.5 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700"
            >
              刷新页面
            </button>
          </div>
        </div>
      </div>
    )
  }
}
