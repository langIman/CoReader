import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWikiStore } from '../../store/useWikiStore'
import CodeSnippet from './CodeSnippet'
import FileTree from './FileTree'

type Geom = { top: number; left: number; width: number; height: number }
type ResizeEdge = 'e' | 's' | 'se' | 'n' | 'w' | 'ne' | 'sw' | 'nw'

const STORAGE_KEY = 'coreader.codeWindow.geom'
const TREE_KEY = 'coreader.codeWindow.treeOpen'
const TREE_WIDTH_KEY = 'coreader.codeWindow.treeWidth'
const MIN_W = 400
const MIN_H = 240
const MIN_TREE_W = 140
const MAX_TREE_W = 400
const DEFAULT_TREE_W = 220

function defaultGeom(): Geom {
  const vw = typeof window !== 'undefined' ? window.innerWidth : 1280
  const vh = typeof window !== 'undefined' ? window.innerHeight : 800
  const width = Math.min(820, Math.round(vw * 0.6))
  const height = Math.min(560, Math.round(vh * 0.65))
  return {
    top: Math.max(16, Math.round((vh - height) / 2)),
    left: Math.max(16, Math.round((vw - width) / 2)),
    width,
    height,
  }
}

function clampGeom(g: Geom): Geom {
  const vw = window.innerWidth
  const vh = window.innerHeight
  const width = Math.max(MIN_W, Math.min(g.width, vw - 8))
  const height = Math.max(MIN_H, Math.min(g.height, vh - 8))
  const left = Math.max(0, Math.min(g.left, vw - 60))
  const top = Math.max(0, Math.min(g.top, vh - 40))
  return { top, left, width, height }
}

function loadGeom(): Geom {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return defaultGeom()
    const parsed = JSON.parse(raw) as Partial<Geom>
    if (
      typeof parsed.top === 'number' &&
      typeof parsed.left === 'number' &&
      typeof parsed.width === 'number' &&
      typeof parsed.height === 'number'
    ) {
      return clampGeom(parsed as Geom)
    }
  } catch {
    // ignore
  }
  return defaultGeom()
}

function saveGeom(g: Geom) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(g))
  } catch {
    // ignore
  }
}

function loadTreeOpen(): boolean {
  try {
    const raw = localStorage.getItem(TREE_KEY)
    if (raw === null) return true
    return raw === '1'
  } catch {
    return true
  }
}

function loadTreeWidth(): number {
  try {
    const v = Number(localStorage.getItem(TREE_WIDTH_KEY))
    if (Number.isFinite(v) && v >= MIN_TREE_W && v <= MAX_TREE_W) return v
  } catch {
    // ignore
  }
  return DEFAULT_TREE_W
}

export default function FloatingCodeWindow() {
  const { open, ref } = useWikiStore((s) => s.codeDrawer)
  const closeCodeDrawer = useWikiStore((s) => s.closeCodeDrawer)
  const openCodeDrawerWithRef = useWikiStore((s) => s.openCodeDrawerWithRef)
  const projectFiles = useWikiStore((s) => s.projectFiles)

  const [geom, setGeom] = useState<Geom>(() => loadGeom())
  const [maximized, setMaximized] = useState(false)
  const [preMaxGeom, setPreMaxGeom] = useState<Geom | null>(null)
  const [treeOpen, setTreeOpen] = useState<boolean>(() => loadTreeOpen())
  const [treeWidth, setTreeWidth] = useState<number>(() => loadTreeWidth())

  const dragKind = useRef<'none' | 'move' | ResizeEdge | 'tree'>('none')
  const dragStart = useRef<{
    x: number
    y: number
    g: Geom
    tw: number
  } | null>(null)

  const onHeaderMouseDown = useCallback(
    (e: React.MouseEvent) => {
      if (maximized) return
      const target = e.target as HTMLElement
      if (target.closest('[data-no-drag]')) return
      e.preventDefault()
      dragKind.current = 'move'
      dragStart.current = { x: e.clientX, y: e.clientY, g: geom, tw: treeWidth }
      document.body.style.cursor = 'move'
      document.body.style.userSelect = 'none'
    },
    [geom, treeWidth, maximized],
  )

  const startResize = useCallback(
    (edge: ResizeEdge) => (e: React.MouseEvent) => {
      if (maximized) return
      e.preventDefault()
      e.stopPropagation()
      dragKind.current = edge
      dragStart.current = { x: e.clientX, y: e.clientY, g: geom, tw: treeWidth }
      document.body.style.cursor = cursorFor(edge)
      document.body.style.userSelect = 'none'
    },
    [geom, treeWidth, maximized],
  )

  const startTreeResize = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      e.stopPropagation()
      dragKind.current = 'tree'
      dragStart.current = { x: e.clientX, y: e.clientY, g: geom, tw: treeWidth }
      document.body.style.cursor = 'col-resize'
      document.body.style.userSelect = 'none'
    },
    [geom, treeWidth],
  )

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (dragKind.current === 'none' || !dragStart.current) return
      const dx = e.clientX - dragStart.current.x
      const dy = e.clientY - dragStart.current.y
      const start = dragStart.current.g
      const startTw = dragStart.current.tw
      const kind = dragKind.current

      if (kind === 'tree') {
        const next = Math.max(
          MIN_TREE_W,
          Math.min(MAX_TREE_W, Math.min(startTw + dx, start.width - 200)),
        )
        setTreeWidth(next)
        return
      }

      if (kind === 'move') {
        setGeom(
          clampGeom({
            ...start,
            top: start.top + dy,
            left: start.left + dx,
          }),
        )
        return
      }

      // resize
      let next = { ...start }
      if (kind.includes('e')) {
        next.width = start.width + dx
      }
      if (kind.includes('w')) {
        next.left = start.left + dx
        next.width = start.width - dx
      }
      if (kind.includes('s')) {
        next.height = start.height + dy
      }
      if (kind.includes('n')) {
        next.top = start.top + dy
        next.height = start.height - dy
      }
      next = clampGeom(next)
      setGeom(next)
    }
    const onUp = () => {
      if (dragKind.current === 'none') return
      const kind = dragKind.current
      dragKind.current = 'none'
      dragStart.current = null
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
      if (kind === 'tree') {
        try {
          localStorage.setItem(TREE_WIDTH_KEY, String(treeWidth))
        } catch {
          // ignore
        }
      } else {
        saveGeom(geom)
      }
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [geom, treeWidth])

  useEffect(() => {
    const onWinResize = () => setGeom((g) => clampGeom(g))
    window.addEventListener('resize', onWinResize)
    return () => window.removeEventListener('resize', onWinResize)
  }, [])

  const filePaths = useMemo(
    () => Object.keys(projectFiles).sort(),
    [projectFiles],
  )

  const pickFile = (path: string) => {
    const content = projectFiles[path]
    if (!content) return
    const lineCount = content.split('\n').length
    openCodeDrawerWithRef({
      file: path,
      start_line: 1,
      end_line: lineCount,
      symbol: null,
    })
  }

  const toggleMaximize = () => {
    if (maximized && preMaxGeom) {
      setGeom(preMaxGeom)
      setMaximized(false)
    } else {
      setPreMaxGeom(geom)
      setGeom({
        top: 16,
        left: 16,
        width: window.innerWidth - 32,
        height: window.innerHeight - 32,
      })
      setMaximized(true)
    }
  }

  const toggleTree = () => {
    setTreeOpen((v) => {
      const next = !v
      try {
        localStorage.setItem(TREE_KEY, next ? '1' : '0')
      } catch {
        // ignore
      }
      return next
    })
  }

  if (!open || !ref) return null

  return (
    <div
      className="fixed z-40 flex flex-col bg-white dark:bg-gray-900 border border-gray-300 dark:border-gray-600 rounded-lg shadow-2xl"
      style={{
        top: geom.top,
        left: geom.left,
        width: geom.width,
        height: geom.height,
      }}
    >
      <header
        className="flex items-center gap-2 px-3 py-1.5 bg-gray-100 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700 rounded-t-lg select-none flex-shrink-0"
        style={{ cursor: maximized ? 'default' : 'move' }}
        onMouseDown={onHeaderMouseDown}
      >
        <button
          data-no-drag
          onClick={toggleTree}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
          title={treeOpen ? '隐藏文件树' : '显示文件树'}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            <path d="M2 3 H10 M2 6 H10 M2 9 H10" strokeLinecap="round" />
          </svg>
        </button>
        <span className="text-gray-400">📄</span>
        <div className="flex items-center gap-2 text-xs min-w-0 flex-1">
          <code className="font-mono text-gray-700 dark:text-gray-300 truncate">
            {ref.file}
          </code>
          <span className="text-gray-400 flex-shrink-0">
            L{ref.start_line}-L{ref.end_line}
          </span>
          {ref.symbol && (
            <span className="text-blue-600 dark:text-blue-400 font-mono flex-shrink-0 truncate">
              · {ref.symbol}
            </span>
          )}
        </div>

        <button
          data-no-drag
          onClick={toggleMaximize}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
          title={maximized ? '还原' : '最大化'}
        >
          <svg width="12" height="12" viewBox="0 0 12 12" fill="none" stroke="currentColor" strokeWidth="1.5">
            {maximized ? (
              <rect x="3" y="3" width="6" height="6" />
            ) : (
              <rect x="2" y="2" width="8" height="8" />
            )}
          </svg>
        </button>

        <button
          data-no-drag
          onClick={closeCodeDrawer}
          className="p-1 rounded hover:bg-gray-200 dark:hover:bg-gray-700 text-gray-500 dark:text-gray-400"
          title="关闭"
        >
          <svg width="14" height="14" viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="2">
            <path d="M3 3 L13 13 M13 3 L3 13" />
          </svg>
        </button>
      </header>

      {/* 主体：tree + code */}
      <div className="flex-1 flex min-h-0 overflow-hidden rounded-b-lg">
        {treeOpen && (
          <>
            <aside
              className="flex-shrink-0 border-r border-gray-200 dark:border-gray-700 bg-gray-50 dark:bg-gray-950/40 overflow-hidden"
              style={{ width: treeWidth }}
            >
              <FileTree
                paths={filePaths}
                currentFile={ref.file}
                onSelect={pickFile}
              />
            </aside>
            <div
              className="w-1 cursor-col-resize bg-gray-200 dark:bg-gray-700 hover:bg-blue-400 dark:hover:bg-blue-500 active:bg-blue-500 transition-colors flex-shrink-0"
              onMouseDown={startTreeResize}
              title="拖动调整文件树宽度"
            />
          </>
        )}
        <div className="flex-1 min-w-0 flex flex-col">
          <CodeSnippet codeRef={ref} />
        </div>
      </div>

      {/* 八向缩放把手 */}
      {!maximized && (
        <>
          {/* 边：n/s/e/w */}
          <div
            onMouseDown={startResize('n')}
            className="absolute left-2 right-2 top-0 h-1 cursor-ns-resize"
          />
          <div
            onMouseDown={startResize('s')}
            className="absolute left-2 right-2 bottom-0 h-1 cursor-ns-resize"
          />
          <div
            onMouseDown={startResize('w')}
            className="absolute top-2 bottom-2 left-0 w-1 cursor-ew-resize"
          />
          <div
            onMouseDown={startResize('e')}
            className="absolute top-2 bottom-2 right-0 w-1 cursor-ew-resize"
          />
          {/* 角：四角 */}
          <div
            onMouseDown={startResize('nw')}
            className="absolute top-0 left-0 w-3 h-3 cursor-nwse-resize"
          />
          <div
            onMouseDown={startResize('ne')}
            className="absolute top-0 right-0 w-3 h-3 cursor-nesw-resize"
          />
          <div
            onMouseDown={startResize('sw')}
            className="absolute bottom-0 left-0 w-3 h-3 cursor-nesw-resize"
          />
          <div
            onMouseDown={startResize('se')}
            className="absolute bottom-0 right-0 w-4 h-4 cursor-nwse-resize flex items-end justify-end pb-0.5 pr-0.5 text-gray-400 dark:text-gray-500 hover:text-gray-600 dark:hover:text-gray-300"
            title="拖拽调整大小"
          >
            <svg width="10" height="10" viewBox="0 0 10 10">
              <path
                d="M8 3 L3 8 M8 6 L6 8"
                stroke="currentColor"
                strokeWidth="1.2"
                strokeLinecap="round"
                fill="none"
              />
            </svg>
          </div>
        </>
      )}
    </div>
  )
}

function cursorFor(edge: ResizeEdge): string {
  switch (edge) {
    case 'n':
    case 's':
      return 'ns-resize'
    case 'e':
    case 'w':
      return 'ew-resize'
    case 'ne':
    case 'sw':
      return 'nesw-resize'
    case 'nw':
    case 'se':
      return 'nwse-resize'
  }
}
