import { useEffect } from 'react'
import { useWikiStore } from './store/useWikiStore'
import UploadView from './components/Upload/UploadView'
import WikiLayout from './components/Wiki/WikiLayout'

export default function App() {
  const wiki = useWikiStore((s) => s.wiki)
  const rehydrating = useWikiStore((s) => s.rehydrating)
  const rehydrateFromStorage = useWikiStore((s) => s.rehydrateFromStorage)

  useEffect(() => {
    void rehydrateFromStorage()
  }, [rehydrateFromStorage])

  // 诊断：抓"白屏后回到上传页"这个偶发 bug 的现场证据。
  // 已加 ErrorBoundary 仍不触发 = 一定是浏览器真的卸载/刷新了页面，而非
  // React 抛错。下次发生时，把 sessionStorage 里的 coreader.unload_reason
  // 和 navigation type（如果走的是 reload）拿出来看就能定位。
  useEffect(() => {
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      try {
        sessionStorage.setItem(
          'coreader.unload_reason',
          JSON.stringify({
            at: new Date().toISOString(),
            // BeforeUnloadEvent.returnValue 触发了浏览器原生确认框就说明有未保存内容
            hasReturnValue: !!e.returnValue,
            url: location.href,
          }),
        )
      } catch {
        // ignore
      }
    }
    window.addEventListener('beforeunload', onBeforeUnload)
    // 启动时打印上一次卸载原因（若有）：刷新后第一时间在控制台看见
    try {
      const last = sessionStorage.getItem('coreader.unload_reason')
      if (last) {
        console.warn('[App] 上一次页面卸载记录:', JSON.parse(last))
        const nav = performance.getEntriesByType('navigation')[0] as
          | PerformanceNavigationTiming
          | undefined
        if (nav) console.warn('[App] 本次导航类型:', nav.type)
      }
    } catch {
      // ignore
    }
    return () => window.removeEventListener('beforeunload', onBeforeUnload)
  }, [])

  if (rehydrating) {
    return (
      <div className="flex items-center justify-center h-screen bg-gray-50 dark:bg-gray-900">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full animate-spin" />
          <p className="text-sm text-gray-600 dark:text-gray-300">正在恢复已保存的项目...</p>
        </div>
      </div>
    )
  }

  return wiki ? <WikiLayout /> : <UploadView />
}
