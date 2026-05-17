/**
 * VSCode 风格的 editor groups 布局状态。
 *
 * 整个右侧区域是一棵递归的 layout tree：
 * - LeafNode：一组 tab，激活其中一个
 * - SplitNode：方向 + 两个子节点 + 各自尺寸
 *
 * M1：仅支持单 leaf（QA / Quiz 共享 tab 头）
 * M2：split 节点 + 嵌套 resize（拟）
 * M3：拖拽 split / merge（拟）
 */

import { create } from 'zustand'

/**
 * Tab 标识：内置 'qa' / 'quiz' 单例 + 'wiki:${pageId}' 表示一个 wiki page tab。
 * 把 pageId 编进 id 里，让多个 wiki tab 各自携带不同 page，无需额外字段。
 */
export type TabId = 'qa' | 'quiz' | `wiki:${string}`

export const WIKI_TAB_PREFIX = 'wiki:' as const

/** 把 pageId 转成 wiki tab id。 */
export function makeWikiTabId(pageId: string): TabId {
  return `${WIKI_TAB_PREFIX}${pageId}` as TabId
}

/** 从 tab id 反查 pageId；非 wiki tab 返回 null。 */
export function getWikiPageId(tabId: TabId): string | null {
  if (typeof tabId !== 'string') return null
  if (!tabId.startsWith(WIKI_TAB_PREFIX)) return null
  return tabId.slice(WIKI_TAB_PREFIX.length)
}

/** 递归遍历，找出第一个 active tab 是 wiki 的 leaf 的 pageId。 */
export function findActiveWikiPageId(node: { kind: string } | null): string | null {
  if (!node) return null
  const n = node as LayoutNode
  if (n.kind === 'leaf') {
    const active = n.activeTab
    if (!active) return null
    return getWikiPageId(active)
  }
  return (
    findActiveWikiPageId(n.children[0])
    || findActiveWikiPageId(n.children[1])
  )
}

/** 整棵树是否包含任意 wiki tab。 */
export function hasAnyWikiTab(node: { kind: string } | null): boolean {
  if (!node) return false
  const n = node as LayoutNode
  if (n.kind === 'leaf') {
    return n.tabs.some((t) => t.startsWith(WIKI_TAB_PREFIX))
  }
  return hasAnyWikiTab(n.children[0]) || hasAnyWikiTab(n.children[1])
}

export interface LeafNode {
  kind: 'leaf'
  id: string
  tabs: TabId[]
  activeTab: TabId | null
}

export interface SplitNode {
  kind: 'split'
  id: string
  direction: 'horizontal' | 'vertical'
  children: [LayoutNode, LayoutNode]
  sizes: [number, number]
}

export type LayoutNode = LeafNode | SplitNode

const STORAGE_KEY = 'coreader.layout.rightPanel'

/** 拖拽 drop 的 5 个区域。center=合并到目标 group；其余=按方向 split。 */
export type SplitDirection = 'left' | 'right' | 'top' | 'bottom' | 'center'

interface LayoutStore {
  root: LayoutNode | null

  // 拖拽状态：当前正在被拖的 tab id（null = 未在拖）
  draggingTab: TabId | null

  // tab actions
  hasTab: (tabId: TabId) => boolean
  openTab: (tabId: TabId) => void
  /** 便捷封装：传入 pageId（不带 wiki: 前缀），自动转成 wiki tab id。 */
  openWikiPage: (pageId: string) => void
  closeTab: (tabId: TabId) => void
  setActiveTab: (groupId: string, tabId: TabId) => void

  // split / merge
  splitTab: (
    sourceTabId: TabId,
    targetGroupId: string,
    direction: SplitDirection,
  ) => void
  setSplitSizes: (splitId: string, sizes: [number, number]) => void

  /**
   * 首次加载默认布局：左侧 wiki + 右侧问答横向分屏。
   * 直接覆盖 root；调用方负责仅在首次（layoutRoot=null）时调用。
   */
  setupDefault: (wikiPageId: string) => void

  // 拖拽
  setDraggingTab: (tabId: TabId | null) => void
}

// ─────────── helpers ───────────

function genId(): string {
  return Math.random().toString(36).slice(2, 10)
}

function findLeafByTab(node: LayoutNode | null, tabId: TabId): LeafNode | null {
  if (!node) return null
  if (node.kind === 'leaf') {
    return node.tabs.includes(tabId) ? node : null
  }
  return findLeafByTab(node.children[0], tabId) || findLeafByTab(node.children[1], tabId)
}

function findFirstLeaf(node: LayoutNode | null): LeafNode | null {
  if (!node) return null
  if (node.kind === 'leaf') return node
  return findFirstLeaf(node.children[0]) || findFirstLeaf(node.children[1])
}

function findLastLeaf(node: LayoutNode | null): LeafNode | null {
  if (!node) return null
  if (node.kind === 'leaf') return node
  return findLastLeaf(node.children[1]) || findLastLeaf(node.children[0])
}

/** 不可变递归更新：把符合 predicate 的 leaf 替换为 mapper(leaf)。 */
function mapLeaves(
  node: LayoutNode,
  predicate: (leaf: LeafNode) => boolean,
  mapper: (leaf: LeafNode) => LeafNode,
): LayoutNode {
  if (node.kind === 'leaf') {
    return predicate(node) ? mapper(node) : node
  }
  return {
    ...node,
    children: [
      mapLeaves(node.children[0], predicate, mapper),
      mapLeaves(node.children[1], predicate, mapper),
    ],
  }
}

/** 递归找含指定 groupId 的 leaf。 */
function findLeafById(node: LayoutNode | null, groupId: string): LeafNode | null {
  if (!node) return null
  if (node.kind === 'leaf') return node.id === groupId ? node : null
  return findLeafById(node.children[0], groupId)
    || findLeafById(node.children[1], groupId)
}

/**
 * 递归移除 tabId；leaf 空了返回 null（让父 split collapse）。
 * 返回更新后的子树或 null。
 */
function removeTabFromTree(node: LayoutNode, tabId: TabId): LayoutNode | null {
  if (node.kind === 'leaf') {
    if (!node.tabs.includes(tabId)) return node
    const filtered = node.tabs.filter((t) => t !== tabId)
    if (filtered.length === 0) return null
    return {
      ...node,
      tabs: filtered,
      activeTab: node.activeTab === tabId ? filtered[0] : node.activeTab,
    }
  }
  const c0 = removeTabFromTree(node.children[0], tabId)
  const c1 = removeTabFromTree(node.children[1], tabId)
  if (c0 && c1) {
    return { ...node, children: [c0, c1] }
  }
  // 一边塌陷：用另一边替换整个 split
  return c0 || c1
}

// ─────────── persistence ───────────

function isLayoutNode(n: unknown): n is LayoutNode {
  if (!n || typeof n !== 'object') return false
  const obj = n as Record<string, unknown>
  if (obj.kind === 'leaf') {
    return Array.isArray(obj.tabs) && typeof obj.id === 'string'
  }
  if (obj.kind === 'split') {
    return (
      Array.isArray(obj.children)
      && obj.children.length === 2
      && isLayoutNode(obj.children[0])
      && isLayoutNode(obj.children[1])
    )
  }
  return false
}

function loadRoot(): LayoutNode | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return isLayoutNode(parsed) ? parsed : null
  } catch {
    return null
  }
}

function persistRoot(root: LayoutNode | null) {
  try {
    if (root) localStorage.setItem(STORAGE_KEY, JSON.stringify(root))
    else localStorage.removeItem(STORAGE_KEY)
  } catch {
    // ignore
  }
}

// ─────────── store ───────────

export const useLayoutStore = create<LayoutStore>((set, get) => ({
  root: loadRoot(),
  draggingTab: null,

  hasTab: (tabId) => findLeafByTab(get().root, tabId) !== null,

  openTab: (tabId) => {
    const { root } = get()
    // 已经在树里：仅激活
    const existing = findLeafByTab(root, tabId)
    if (existing) {
      const newRoot = mapLeaves(
        root!,
        (l) => l.id === existing.id,
        (l) => ({ ...l, activeTab: tabId }),
      )
      persistRoot(newRoot)
      set({ root: newRoot })
      return
    }
    // 树空：建一个新 leaf
    if (!root) {
      const newLeaf: LeafNode = {
        kind: 'leaf',
        id: genId(),
        tabs: [tabId],
        activeTab: tabId,
      }
      persistRoot(newLeaf)
      set({ root: newLeaf })
      return
    }
    const isWikiTab = (tabId as string).startsWith(WIKI_TAB_PREFIX)

    if (isWikiTab) {
      // wiki tab 插入第一个 leaf（左侧）
      const target = findFirstLeaf(root)
      if (!target) return
      const newRoot = mapLeaves(
        root,
        (l) => l.id === target.id,
        (l) => ({ ...l, tabs: [...l.tabs, tabId], activeTab: tabId }),
      )
      persistRoot(newRoot)
      set({ root: newRoot })
      return
    }

    // qa / quiz：找最后一个 leaf
    const target = findLastLeaf(root)
    if (!target) return

    // 若目标 leaf 全是 wiki tab，将整棵树包入新 split，新 tab 置于右侧新叶
    const targetOnlyWiki = target.tabs.every((t) => (t as string).startsWith(WIKI_TAB_PREFIX))
    if (targetOnlyWiki) {
      const newLeaf: LeafNode = { kind: 'leaf', id: genId(), tabs: [tabId], activeTab: tabId }
      const newRoot: SplitNode = {
        kind: 'split',
        id: genId(),
        direction: 'horizontal',
        children: [root, newLeaf],
        sizes: [60, 40],
      }
      persistRoot(newRoot)
      set({ root: newRoot })
      return
    }

    // 目标 leaf 已有 qa/quiz：正常插入
    const newRoot = mapLeaves(
      root,
      (l) => l.id === target.id,
      (l) => ({ ...l, tabs: [...l.tabs, tabId], activeTab: tabId }),
    )
    persistRoot(newRoot)
    set({ root: newRoot })
  },

  openWikiPage: (pageId) => {
    if (!pageId) return
    get().openTab(makeWikiTabId(pageId))
  },

  closeTab: (tabId) => {
    const { root } = get()
    if (!root) return
    const newRoot = removeTabFromTree(root, tabId)
    persistRoot(newRoot)
    set({ root: newRoot })
  },

  setActiveTab: (groupId, tabId) => {
    const { root } = get()
    if (!root) return
    const newRoot = mapLeaves(
      root,
      (l) => l.id === groupId,
      (l) => (l.tabs.includes(tabId) ? { ...l, activeTab: tabId } : l),
    )
    persistRoot(newRoot)
    set({ root: newRoot })
  },

  splitTab: (sourceTabId, targetGroupId, direction) => {
    const { root } = get()
    if (!root) return

    // 1. 校验：target group 存在
    const target = findLeafById(root, targetGroupId)
    if (!target) return

    // 2. 从原 leaf 移除 sourceTab（先 immutably 改树，再处理 collapse）
    const removed = removeTabFromTree(root, sourceTabId)

    // 边界：source 就在 target 里，且是唯一 tab → 移除会让 target 消失
    // 这种情况什么也不做
    if (!removed) return
    // 校验：移除后 target 是否还在
    const targetAfterRemove = findLeafById(removed, targetGroupId)
    if (!targetAfterRemove) {
      // target 在移除过程中被 collapse 了（说明 source 就在 target 里且为唯一 tab）
      // 直接放弃这次 split
      return
    }

    // 3. 插入：根据 direction 改造 target leaf 为新 split 或合并
    const replaceTarget = (n: LayoutNode): LayoutNode => {
      if (n.kind === 'leaf') {
        if (n.id !== targetGroupId) return n
        if (direction === 'center') {
          return {
            ...n,
            tabs: [...n.tabs, sourceTabId],
            activeTab: sourceTabId,
          }
        }
        const newLeaf: LeafNode = {
          kind: 'leaf',
          id: genId(),
          tabs: [sourceTabId],
          activeTab: sourceTabId,
        }
        const splitDirection: 'horizontal' | 'vertical'
          = direction === 'left' || direction === 'right'
            ? 'horizontal'
            : 'vertical'
        const sourceFirst = direction === 'left' || direction === 'top'
        const splitNode: SplitNode = {
          kind: 'split',
          id: genId(),
          direction: splitDirection,
          children: sourceFirst ? [newLeaf, n] : [n, newLeaf],
          sizes: [50, 50],
        }
        return splitNode
      }
      return {
        ...n,
        children: [replaceTarget(n.children[0]), replaceTarget(n.children[1])],
      }
    }
    const newRoot = replaceTarget(removed)
    persistRoot(newRoot)
    set({ root: newRoot })
  },

  setSplitSizes: (splitId, sizes) => {
    const { root } = get()
    if (!root) return
    const update = (n: LayoutNode): LayoutNode => {
      if (n.kind === 'leaf') return n
      if (n.id === splitId) {
        // 防止某一边过小（min 100）
        const total = sizes[0] + sizes[1]
        const min = 100
        let [a, b] = sizes
        if (a < min) {
          a = min
          b = total - a
        }
        if (b < min) {
          b = min
          a = total - b
        }
        return { ...n, sizes: [a, b] }
      }
      return { ...n, children: [update(n.children[0]), update(n.children[1])] }
    }
    const newRoot = update(root)
    persistRoot(newRoot)
    set({ root: newRoot })
  },

  setDraggingTab: (tabId) => set({ draggingTab: tabId }),

  setupDefault: (wikiPageId) => {
    const wikiTab = makeWikiTabId(wikiPageId)
    const newRoot: SplitNode = {
      kind: 'split',
      id: genId(),
      direction: 'horizontal',
      children: [
        {
          kind: 'leaf',
          id: genId(),
          tabs: [wikiTab],
          activeTab: wikiTab,
        },
        {
          kind: 'leaf',
          id: genId(),
          tabs: ['qa'],
          activeTab: 'qa',
        },
      ],
      // 60/40：左侧 wiki 占主要空间，右侧问答辅助
      sizes: [60, 40],
    }
    persistRoot(newRoot)
    set({ root: newRoot })
  },
}))
