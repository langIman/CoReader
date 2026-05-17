import { useCallback, useEffect, useRef, useState } from 'react'
import { loadPendingTask, persistPendingTask, useWikiStore } from '../../store/useWikiStore'
import { getWikiDocument, pollWikiStatus, startWikiGeneration, uploadProject } from '../../services/api'
import ThemeToggle from '../common/ThemeToggle'
import WikiProgressTimeline from './WikiProgressTimeline'

type Phase = 'idle' | 'uploading' | 'generating' | 'loading' | 'error'

function formatElapsed(ms: number): string {
  const total = Math.max(0, Math.floor(ms / 1000))
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
}

export default function UploadView() {
  const {
    setProject,
    setGenerateTaskId,
    setGenerateStatus,
    setGenerateEvents,
    setWiki,
    setLastGenerationDurationMs,
  } = useWikiStore()
  const projectName = useWikiStore((s) => s.projectName)
  const generateEvents = useWikiStore((s) => s.generateEvents)
  const [eventsReceivedAt, setEventsReceivedAt] = useState<number>(() => Date.now())
  const folderInputRef = useRef<HTMLInputElement>(null)
  const [phase, setPhase] = useState<Phase>('idle')
  const [message, setMessage] = useState<string>('')
  const [dragging, setDragging] = useState(false)
  const [, setDragCounter] = useState(0)
  const [startedAt, setStartedAt] = useState<number | null>(null)
  const [elapsedMs, setElapsedMs] = useState<number>(0)

  useEffect(() => {
    if (startedAt === null) return
    const tick = () => setElapsedMs(Date.now() - startedAt)
    tick()
    const id = window.setInterval(tick, 1000)
    return () => window.clearInterval(id)
  }, [startedAt])

  const loadExistingWiki = useCallback(
    async (name: string) => {
      try {
        setPhase('loading')
        setMessage(`尝试加载已生成的 Wiki（${name}）...`)
        const doc = await getWikiDocument(name)
        setWiki(doc)
      } catch (err) {
        console.error('[loadExistingWiki] 失败:', err)
        setPhase('error')
        setMessage(err instanceof Error ? err.message : '加载失败')
      }
    },
    [setWiki]
  )

  useEffect(() => {
    const prevent = (e: DragEvent) => {
      e.preventDefault()
      e.stopPropagation()
    }
    document.addEventListener('dragover', prevent)
    document.addEventListener('drop', prevent)
    return () => {
      document.removeEventListener('dragover', prevent)
      document.removeEventListener('drop', prevent)
    }
  }, [])

  // 接管「轮询 → 加载文档 → 入 store」三段。被两条入口共用：
  //   1. runPipeline：本次会话内新发起的上传
  //   2. 启动时 resumePending：上次未完成的任务（页面刷新/崩溃后）
  const watchTask = useCallback(
    async (params: { taskId: string; projectName: string; pipelineStart: number }) => {
      const { taskId, projectName, pipelineStart } = params
      setGenerateTaskId(taskId)
      setGenerateStatus('pending')
      setMessage('正在分析项目结构（概览页 + 模块页）...')
      setGenerateEvents([])
      setEventsReceivedAt(Date.now())

      const final = await pollWikiStatus(taskId, (s) => {
        setGenerateStatus(s.status, s.message)
        setGenerateEvents(s.events ?? [])
        setEventsReceivedAt(Date.now())
        if (s.status === 'running') setMessage('LLM 生成中...')
      })
      if (final.status === 'failed') {
        throw new Error(final.message || 'Wiki 生成失败')
      }

      setPhase('loading')
      setMessage('加载 Wiki 文档...')
      const doc = await getWikiDocument(projectName)
      const totalMs = Date.now() - pipelineStart
      setLastGenerationDurationMs(totalMs)
      setStartedAt(null)
      setWiki(doc)
    },
    [setGenerateTaskId, setGenerateStatus, setGenerateEvents, setLastGenerationDurationMs, setWiki]
  )

  const runPipeline = useCallback(
    async (files: FileList | { path: string; file: File }[]) => {
      const pipelineStart = Date.now()
      setStartedAt(pipelineStart)
      setElapsedMs(0)
      setLastGenerationDurationMs(null)
      try {
        setPhase('uploading')
        setMessage('上传项目文件...')
        const uploaded = await uploadProject(files)
        const filesMap: Record<string, string> = {}
        for (const f of uploaded.files) filesMap[f.path] = f.content
        setProject(uploaded.project_name, filesMap)

        setPhase('generating')
        setMessage('启动 Wiki 生成...')
        const task = await startWikiGeneration(uploaded.project_name)
        // 任务持久化：即使现在页面刷新，下次启动会自动恢复轮询
        persistPendingTask({
          task_id: task.task_id,
          project_name: uploaded.project_name,
          started_at: pipelineStart,
        })

        await watchTask({
          taskId: task.task_id,
          projectName: uploaded.project_name,
          pipelineStart,
        })
        persistPendingTask(null)
      } catch (err) {
        console.error('[runPipeline] 流水线失败:', err)
        persistPendingTask(null)
        setStartedAt(null)
        setPhase('error')
        setMessage(err instanceof Error ? err.message : '未知错误')
      }
    },
    [setProject, watchTask]
  )

  // 启动时：若 localStorage 里有未完成任务，自动恢复轮询。
  // 这一步把"白屏刷新"这个偶发 bug 在体感上消灭：用户刷新或浏览器自己崩了，
  // 重进页面后会从模块划分/页面生成的当前状态继续走，不需要重新上传。
  useEffect(() => {
    const pending = loadPendingTask()
    if (!pending) return
    // 项目源码本地丢了（state 在内存里），但后端 SQLite 仍有，让用户能继续看进度
    void (async () => {
      console.info('[resumePending] 检测到未完成任务，恢复轮询:', pending)
      const resumeStart = pending.started_at || Date.now()
      setStartedAt(resumeStart)
      setLastGenerationDurationMs(null)
      setPhase('generating')
      setMessage('恢复未完成的 Wiki 生成任务...')
      try {
        await watchTask({
          taskId: pending.task_id,
          projectName: pending.project_name,
          pipelineStart: resumeStart,
        })
        persistPendingTask(null)
      } catch (err) {
        console.error('[resumePending] 恢复失败:', err)
        persistPendingTask(null)
        setStartedAt(null)
        setPhase('error')
        setMessage(err instanceof Error ? err.message : '恢复任务失败')
      }
    })()
    // 仅启动时跑一次
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const handleFolderSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files
      if (!files || files.length === 0) return
      await runPipeline(files)
      e.target.value = ''
    },
    [runPipeline]
  )

  const handleDragEnter = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragCounter((c) => c + 1)
    setDragging(true)
  }, [])

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragCounter((c) => {
      const next = c - 1
      if (next <= 0) setDragging(false)
      return Math.max(0, next)
    })
  }, [])

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault()
      setDragging(false)
      setDragCounter(0)
      const items = e.dataTransfer.items
      if (!items || items.length === 0) return

      const firstEntry = items[0].webkitGetAsEntry?.()
      if (firstEntry?.isDirectory) {
        const files = await collectFilesFromDrop(items)
        if (files.length === 0) {
          alert('文件夹中未找到 .py 文件')
          return
        }
        await runPipeline(files)
      } else {
        alert('请拖入整个项目文件夹')
      }
    },
    [runPipeline]
  )

  const busy = phase === 'uploading' || phase === 'generating' || phase === 'loading'

  return (
    <div
      className="flex flex-col h-screen bg-gray-50 dark:bg-gray-900 relative"
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={(e) => e.preventDefault()}
      onDrop={handleDrop}
    >
      <header className="flex items-center justify-between px-6 py-3 border-b border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800">
        <h1 className="text-lg font-semibold text-gray-800 dark:text-gray-100">
          CoReader · 代码知识库
        </h1>
        <ThemeToggle />
      </header>

      <main className="flex-1 flex items-center justify-center p-6">
        <div className="w-full max-w-xl bg-white dark:bg-gray-800 rounded-2xl shadow-sm border border-gray-200 dark:border-gray-700 p-8">
          <div className="text-center">
            <div className="text-5xl mb-3">📚</div>
            <h2 className="text-xl font-semibold text-gray-800 dark:text-gray-100 mb-2">
              上传 Python 项目
            </h2>
            <p className="text-sm text-gray-500 dark:text-gray-400 mb-6">
              拖入项目文件夹或点击下方按钮选择；上传后自动生成 Wiki 文档
            </p>

            {!busy && phase !== 'error' && (
              <button
                onClick={() => folderInputRef.current?.click()}
                className="px-5 py-2 bg-blue-600 text-white rounded-lg hover:bg-blue-700 transition-colors text-sm font-medium"
              >
                选择项目文件夹
              </button>
            )}

            {busy && (
              <div className="flex flex-col items-center gap-3 py-4">
                <div className="w-8 h-8 border-3 border-blue-500 border-t-transparent rounded-full animate-spin" />
                <p className="text-sm text-gray-600 dark:text-gray-300">{message}</p>
                <div
                  className="font-mono text-2xl tabular-nums text-blue-600 dark:text-blue-300 tracking-wider"
                  aria-live="polite"
                >
                  ⏱ {formatElapsed(elapsedMs)}
                </div>
                {phase === 'generating' && (
                  <WikiProgressTimeline
                    events={generateEvents}
                    nowMs={Date.now()}
                    eventsReceivedAt={eventsReceivedAt}
                  />
                )}
                {phase !== 'generating' && (
                  <p className="text-xs text-gray-400 dark:text-gray-500">
                    中大项目可能需要 30 秒到几分钟
                  </p>
                )}
              </div>
            )}

            {phase === 'error' && (
              <div className="py-4">
                <p className="text-sm text-red-600 dark:text-red-400 mb-3 whitespace-pre-wrap break-words">
                  {message}
                </p>
                {projectName && (
                  <p className="text-xs text-gray-500 dark:text-gray-400 mb-3">
                    已上传项目：<code className="font-mono">{projectName}</code>
                    ——后端若生成成功可直接加载
                  </p>
                )}
                <div className="flex justify-center gap-2 flex-wrap">
                  {projectName && (
                    <button
                      onClick={() => loadExistingWiki(projectName)}
                      className="px-4 py-1.5 text-sm text-white bg-blue-600 rounded-lg hover:bg-blue-700"
                    >
                      尝试加载已生成的 Wiki
                    </button>
                  )}
                  <button
                    onClick={() => {
                      setPhase('idle')
                      setMessage('')
                    }}
                    className="px-4 py-1.5 text-sm text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-700 rounded-lg hover:bg-gray-200 dark:hover:bg-gray-600"
                  >
                    重新上传
                  </button>
                </div>
              </div>
            )}
          </div>

          <input
            ref={folderInputRef}
            type="file"
            className="hidden"
            onChange={handleFolderSelect}
            {...({ webkitdirectory: '', directory: '' } as React.InputHTMLAttributes<HTMLInputElement>)}
          />
        </div>
      </main>

      {dragging && (
        <div className="absolute inset-0 z-50 bg-blue-500/10 border-4 border-dashed border-blue-400 flex items-center justify-center pointer-events-none">
          <div className="bg-white dark:bg-gray-800 rounded-xl shadow-lg px-8 py-6 text-center">
            <div className="text-3xl mb-2">📁</div>
            <p className="text-lg font-medium text-gray-700 dark:text-gray-200">松开以上传</p>
            <p className="text-sm text-gray-400 mt-1">整个项目文件夹</p>
          </div>
        </div>
      )}
    </div>
  )
}

async function collectFilesFromDrop(
  items: DataTransferItemList
): Promise<{ path: string; file: File }[]> {
  const results: { path: string; file: File }[] = []

  async function readEntry(entry: FileSystemEntry, basePath: string) {
    if (entry.isFile) {
      const file = await new Promise<File>((resolve) =>
        (entry as FileSystemFileEntry).file(resolve)
      )
      results.push({ path: basePath + entry.name, file })
    } else if (entry.isDirectory) {
      const reader = (entry as FileSystemDirectoryEntry).createReader()
      const entries: FileSystemEntry[] = []
      while (true) {
        const batch = await new Promise<FileSystemEntry[]>((resolve) =>
          reader.readEntries(resolve)
        )
        if (batch.length === 0) break
        entries.push(...batch)
      }
      for (const child of entries) {
        await readEntry(child, basePath + entry.name + '/')
      }
    }
  }

  for (let i = 0; i < items.length; i++) {
    const entry = items[i].webkitGetAsEntry?.()
    if (entry) await readEntry(entry, '')
  }
  return results
}
