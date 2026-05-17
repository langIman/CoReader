import { useRef } from 'react'
import type { LayoutNode, SplitNode } from '../../store/useLayoutStore'
import { useLayoutStore } from '../../store/useLayoutStore'
import Resizer from '../common/Resizer'
import TabGroup from './TabGroup'

interface Props {
  node: LayoutNode
}

/**
 * 递归渲染 layout tree。
 *
 * - leaf：直接渲染 TabGroup
 * - split：按 direction 横/纵排列两个子节点 + 中间一个 Resizer
 */
export default function LayoutTree({ node }: Props) {
  if (node.kind === 'leaf') {
    return <TabGroup leaf={node} />
  }
  return <SplitContainer node={node} />
}

function SplitContainer({ node }: { node: SplitNode }) {
  const setSplitSizes = useLayoutStore((s) => s.setSplitSizes)
  const isHorizontal = node.direction === 'horizontal'
  const containerRef = useRef<HTMLDivElement>(null)
  const total = node.sizes[0] + node.sizes[1]

  // 用 flex-grow 比例，让两侧按 sizes 分配空间，整体随容器缩放
  const flex0 = node.sizes[0] / total
  const flex1 = node.sizes[1] / total

  // delta 是像素偏移，需换算成与 sizes 同单位的比例偏移
  const onResize = (delta: number) => {
    const el = containerRef.current
    if (!el) return
    const containerPx = isHorizontal ? el.offsetWidth : el.offsetHeight
    if (!containerPx) return
    const deltaPct = (delta / containerPx) * total
    setSplitSizes(node.id, [node.sizes[0] + deltaPct, node.sizes[1] - deltaPct])
  }

  return (
    <div
      ref={containerRef}
      className={`flex h-full w-full min-h-0 min-w-0 ${
        isHorizontal ? 'flex-row' : 'flex-col'
      }`}
    >
      <div
        className="min-h-0 min-w-0 overflow-hidden"
        style={{ flexGrow: flex0, flexShrink: 1, flexBasis: 0 }}
      >
        <LayoutTree node={node.children[0]} />
      </div>
      <Resizer
        direction={isHorizontal ? 'horizontal' : 'vertical'}
        onDrag={onResize}
      />
      <div
        className="min-h-0 min-w-0 overflow-hidden"
        style={{ flexGrow: flex1, flexShrink: 1, flexBasis: 0 }}
      >
        <LayoutTree node={node.children[1]} />
      </div>
    </div>
  )
}
