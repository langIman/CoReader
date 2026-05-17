import { useState } from 'react'
import type { SplitDirection, TabId } from '../../store/useLayoutStore'
import { useLayoutStore } from '../../store/useLayoutStore'

interface Props {
  groupId: string
  /** 该 group 当前的 tabs，用于判断是否 source==唯一 tab 自托（无意义 drop 不响应） */
  groupTabs: TabId[]
}

/**
 * 拖拽时显示的 5 区域 drop zone。
 *
 * 几何（基于 group 容器）：
 *   ┌────────────┐
 *   │    top     │  上 25%
 *   ├──┬──────┬──┤
 *   │le│center│ri│  中间 50% 高，左/右各 25% 宽，center 50% 宽
 *   ├──┴──────┴──┤
 *   │   bottom   │  下 25%
 *   └────────────┘
 *
 * 仅在 layoutStore.draggingTab !== null 时渲染。
 *
 * 实现要点：
 * - **不依赖 dataTransfer**（自定义 mime 在 dragover 阶段读取不可靠）
 * - 拖拽源信息全程通过 store 的 draggingTab state 传递
 * - 每个分区的 onDragOver 必须 preventDefault，否则浏览器禁止 drop
 */
export default function DropZones({ groupId, groupTabs }: Props) {
  const draggingTab = useLayoutStore((s) => s.draggingTab)
  const splitTab = useLayoutStore((s) => s.splitTab)
  const setDraggingTab = useLayoutStore((s) => s.setDraggingTab)
  const [hover, setHover] = useState<SplitDirection | null>(null)

  // draggingTab 变 null → DropZones unmount，下次 mount 时 hover 自动重置为 initial null。
  // 所以无需 useEffect 来清理 hover。
  if (draggingTab === null) return null

  // source==唯一 target tab：拖到自己 group 没意义，不响应（避免空 group 闪烁）
  const isSelfOnly = groupTabs.length === 1 && groupTabs[0] === draggingTab

  const onDragOver = (direction: SplitDirection) => (e: React.DragEvent) => {
    if (isSelfOnly) return
    // 必须 preventDefault 才能让 drop 触发——这是 HTML5 drag-drop 的核心约定
    e.preventDefault()
    e.dataTransfer.dropEffect = 'move'
    if (hover !== direction) setHover(direction)
  }

  const onDrop = (direction: SplitDirection) => (e: React.DragEvent) => {
    e.preventDefault()
    if (isSelfOnly) {
      setDraggingTab(null)
      return
    }
    // 用 store state 而非 dataTransfer.getData（更可靠）
    if (draggingTab) {
      splitTab(draggingTab, groupId, direction)
    }
    setDraggingTab(null)
  }

  // 全部用 inline style，绕过 Tailwind 编译可能的问题。
  // inactive zone 完全透明（仅存在用于接收 dragover），active 时蓝色高亮 + 描边。
  const zoneStyle = (
    pos: React.CSSProperties,
    direction: SplitDirection,
  ): React.CSSProperties => ({
    position: 'absolute',
    pointerEvents: 'auto',
    transition: 'background-color 120ms',
    background:
      hover === direction ? 'rgba(59, 130, 246, 0.25)' : 'transparent',
    outline:
      hover === direction ? '2px solid rgb(59, 130, 246)' : 'none',
    outlineOffset: '-2px',
    ...pos,
  })

  return (
    <div
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        left: 0,
        zIndex: 20,
        pointerEvents: 'none',
      }}
    >
      <div
        style={zoneStyle({ top: 0, left: 0, right: 0, height: '25%' }, 'top')}
        onDragOver={onDragOver('top')}
        onDrop={onDrop('top')}
      />
      <div
        style={zoneStyle({ bottom: 0, left: 0, right: 0, height: '25%' }, 'bottom')}
        onDragOver={onDragOver('bottom')}
        onDrop={onDrop('bottom')}
      />
      <div
        style={zoneStyle({ top: '25%', bottom: '25%', left: 0, width: '25%' }, 'left')}
        onDragOver={onDragOver('left')}
        onDrop={onDrop('left')}
      />
      <div
        style={zoneStyle({ top: '25%', bottom: '25%', right: 0, width: '25%' }, 'right')}
        onDragOver={onDragOver('right')}
        onDrop={onDrop('right')}
      />
      <div
        style={zoneStyle(
          { top: '25%', bottom: '25%', left: '25%', right: '25%' },
          'center',
        )}
        onDragOver={onDragOver('center')}
        onDrop={onDrop('center')}
      />
    </div>
  )
}
