import { useCallback, useEffect, useRef } from 'react'

interface ResizerProps {
  /**
   * 拖动回调：传入鼠标本次 move 的位移（px）。
   * - direction='horizontal'：传入 dx（水平位移），调用方调整左右宽度
   * - direction='vertical'：传入 dy（垂直位移），调用方调整上下高度
   */
  onDrag: (delta: number) => void
  /** 默认 horizontal（左右拖把手）。vertical 用于上下分栏 split。 */
  direction?: 'horizontal' | 'vertical'
  title?: string
}

/**
 * 共享的拖把手——VSCode 风格的 splitter。
 *
 * 1.5px 宽（横向）或高（纵向），hover 高亮蓝色，dragging 时锁定 body cursor + userSelect。
 * 仅处理事件，不持有任何尺寸状态——状态由调用方 store 管理。
 */
export default function Resizer({
  onDrag,
  direction = 'horizontal',
  title,
}: ResizerProps) {
  const dragging = useRef(false)
  const lastPos = useRef(0)
  const onDragRef = useRef(onDrag)
  const directionRef = useRef(direction)

  // 保持回调/方向最新引用，避免 useEffect 反复重订阅 mousemove/mouseup
  useEffect(() => {
    onDragRef.current = onDrag
  }, [onDrag])
  useEffect(() => {
    directionRef.current = direction
  }, [direction])

  const onMouseDown = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragging.current = true
      lastPos.current = direction === 'horizontal' ? e.clientX : e.clientY
      document.body.style.cursor
        = direction === 'horizontal' ? 'col-resize' : 'row-resize'
      document.body.style.userSelect = 'none'
    },
    [direction],
  )

  useEffect(() => {
    const onMove = (e: MouseEvent) => {
      if (!dragging.current) return
      const cur = directionRef.current === 'horizontal' ? e.clientX : e.clientY
      const delta = cur - lastPos.current
      lastPos.current = cur
      onDragRef.current(delta)
    }
    const onUp = () => {
      if (!dragging.current) return
      dragging.current = false
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
    return () => {
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
    }
  }, [])

  const sizing
    = direction === 'horizontal'
      ? 'w-1.5 cursor-col-resize'
      : 'h-1.5 cursor-row-resize'

  return (
    <div
      role="separator"
      aria-orientation={direction === 'horizontal' ? 'vertical' : 'horizontal'}
      title={title}
      onMouseDown={onMouseDown}
      className={`${sizing} bg-gray-200 dark:bg-gray-700 hover:bg-blue-400 dark:hover:bg-blue-500 active:bg-blue-500 transition-colors flex-shrink-0 z-10`}
    />
  )
}
