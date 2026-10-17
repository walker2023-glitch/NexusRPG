/**
 * Chronos Timeline — DAG visualisation of the branching narrative commit history.
 *
 * Required packages: reactflow, dagre, @types/dagre (same as /map)
 */
'use client'

import React, {
  useState,
  useCallback,
  useEffect,
  useMemo,
} from 'react'
import { useRouter } from 'next/navigation'
import ReactFlow, {
  Node,
  Edge,
  Controls,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  type NodeProps,
  type NodeMouseHandler,
} from 'reactflow'
import 'reactflow/dist/style.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const API = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
const NODE_W = 200
const NODE_H = 72

// ─── Types ────────────────────────────────────────────────────────────────────

interface CommitMeta {
  commit_id: string
  parent_id: string | null
  depth: number
  branch_label: string | null
  choice_taken: string | null
  is_snapshot: boolean
  timestamp: number
  scene_preview: string
}

interface CommitNodeData extends CommitMeta {
  isHead: boolean
}

interface Toast {
  id: string
  message: string
  kind: 'success' | 'error' | 'info'
}

// ─── Helpers ──────────────────────────────────────────────────────────────────

function fmtTime(ts: number): string {
  return new Date(ts * 1000).toLocaleString(undefined, {
    dateStyle: 'short',
    timeStyle: 'short',
  })
}

function ancestorsOf(allMeta: CommitMeta[], tipId: string): Set<string> {
  const byId = new Map(allMeta.map(m => [m.commit_id, m]))
  const reachable = new Set<string>()
  const queue: string[] = [tipId]
  while (queue.length) {
    const id = queue.pop()!
    if (reachable.has(id)) continue
    reachable.add(id)
    const parent = byId.get(id)?.parent_id
    if (parent) queue.push(parent)
  }
  return reachable
}

// ─── Dagre layout (left-to-right) ────────────────────────────────────────────

function applyDagreHoriz(
  nodes: Node[],
  edges: Edge[],
): { nodes: Node[]; edges: Edge[] } {
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const dagre = require('dagre') as typeof import('dagre')
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'LR', ranksep: 70, nodesep: 36 })
  nodes.forEach(n => g.setNode(n.id, { width: NODE_W, height: NODE_H }))
  edges.forEach(e => g.setEdge(e.source, e.target))
  dagre.layout(g)
  return {
    nodes: nodes.map(n => {
      const pos = g.node(n.id)
      return { ...n, position: { x: pos.x - NODE_W / 2, y: pos.y - NODE_H / 2 } }
    }),
    edges,
  }
}

// ─── Commit meta → React Flow elements ───────────────────────────────────────

function toRFElements(
  metas: CommitMeta[],
  headIds: Set<string>,
): { nodes: Node<CommitNodeData>[]; edges: Edge[] } {
  const rawNodes: Node<CommitNodeData>[] = metas.map((m, i) => ({
    id: m.commit_id,
    type: 'commitNode',
    position: { x: m.depth * 240, y: i * 90 }, // fallback grid
    data: { ...m, isHead: headIds.has(m.commit_id) },
  }))

  const rawEdges: Edge[] = metas
    .filter(m => m.parent_id)
    .map(m => ({
      id: `${m.parent_id}→${m.commit_id}`,
      source: m.parent_id!,
      target: m.commit_id,
      style: { stroke: '#475569', strokeWidth: 1.5 },
      animated: false,
    }))

  return applyDagreHoriz(rawNodes, rawEdges) as { nodes: Node<CommitNodeData>[]; edges: Edge[] }
}

// ─── Custom commit node ───────────────────────────────────────────────────────

function CommitNode({ data }: NodeProps<CommitNodeData>) {
  const isSnap = data.is_snapshot
  const bg   = isSnap ? '#92400E' : '#1E3A8A'   // amber-800 / blue-900
  const accent = isSnap ? '#FCD34D' : '#93C5FD'  // amber-300 / blue-300
  const badge  = isSnap ? 'S' : 'D'

  return (
    <div className="relative" style={{ width: NODE_W }}>
      {/* HEAD dashed ring */}
      {data.isHead && (
        <div
          className="absolute pointer-events-none rounded-xl"
          style={{
            inset: -5,
            border: '2px dashed #2DD4BF',  // teal-400
          }}
        />
      )}

      <div
        className="rounded-xl px-3 py-2 flex flex-col gap-1 text-white shadow-lg"
        style={{ background: bg, border: `1px solid ${accent}22`, minHeight: NODE_H }}
      >
        <Handle type="target" position={Position.Left} style={{ background: accent }} />

        {/* Top row: badge + short ID */}
        <div className="flex items-center gap-1.5">
          <span
            className="w-5 h-5 rounded text-[10px] font-bold flex items-center justify-center shrink-0"
            style={{ background: accent + '33', color: accent }}
          >
            {badge}
          </span>
          <span className="font-mono text-[10px] text-slate-400 truncate flex-1">
            {data.commit_id.slice(0, 10)}
          </span>
          <span className="text-[10px] text-slate-500">d{data.depth}</span>
        </div>

        {/* Scene preview */}
        <p className="text-[11px] leading-tight text-slate-300 line-clamp-2">
          {data.scene_preview || '(no preview)'}
        </p>

        {/* Branch label */}
        {data.branch_label && (
          <span className="text-[10px] text-teal-400 font-mono truncate">
            ⑂ {data.branch_label}
          </span>
        )}

        <Handle type="source" position={Position.Right} style={{ background: accent }} />
      </div>
    </div>
  )
}

const nodeTypes = { commitNode: CommitNode }

// ─── Commit detail panel ──────────────────────────────────────────────────────

function CommitPanel({
  commit,
  onClose,
  onCheckout,
  onFork,
}: {
  commit: CommitMeta
  onClose: () => void
  onCheckout: () => Promise<void>
  onFork: () => Promise<void>
}) {
  const [checkingOut, setCheckingOut] = useState(false)
  const [forking, setForking] = useState(false)

  const handleCheckout = async () => { setCheckingOut(true); await onCheckout(); setCheckingOut(false) }
  const handleFork     = async () => { setForking(true);     await onFork();     setForking(false) }

  return (
    <div className="fixed top-0 right-0 h-full w-80 bg-slate-900 border-l border-slate-700 shadow-2xl z-30 flex flex-col">
      {/* Header */}
      <div className="flex items-start justify-between px-4 py-3 border-b border-slate-700">
        <div className="space-y-0.5">
          <p className="text-white font-mono text-xs">{commit.commit_id.slice(0, 16)}…</p>
          <p className="text-slate-400 text-xs">{fmtTime(commit.timestamp)}</p>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white mt-0.5">✕</button>
      </div>

      <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4 text-sm">
        {/* Type + depth badges */}
        <div className="flex gap-2 flex-wrap">
          <span className={`px-2 py-0.5 rounded text-xs font-mono ${commit.is_snapshot ? 'bg-amber-900 text-amber-300' : 'bg-blue-900 text-blue-300'}`}>
            {commit.is_snapshot ? '⬡ Snapshot' : '△ Delta'}
          </span>
          <span className="px-2 py-0.5 rounded text-xs font-mono bg-slate-800 text-slate-400">
            depth {commit.depth}
          </span>
        </div>

        {/* Scene preview */}
        <div>
          <p className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">Scene preview</p>
          <p className="text-slate-300 leading-relaxed">{commit.scene_preview || '—'}</p>
        </div>

        {commit.choice_taken && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">Choice taken</p>
            <p className="text-green-300 italic">"{commit.choice_taken}"</p>
          </div>
        )}

        {commit.branch_label && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">Branch</p>
            <p className="text-teal-300 font-mono">{commit.branch_label}</p>
          </div>
        )}

        {commit.parent_id && (
          <div>
            <p className="text-xs text-slate-500 uppercase tracking-wider mb-1.5">Parent</p>
            <p className="text-slate-500 font-mono text-xs">{commit.parent_id}</p>
          </div>
        )}
      </div>

      {/* Actions */}
      <div className="px-4 py-4 border-t border-slate-700 space-y-2">
        <button
          onClick={handleCheckout}
          disabled={checkingOut}
          className="w-full py-2 rounded-lg bg-teal-700 hover:bg-teal-600 disabled:opacity-50 text-white font-medium text-sm"
        >
          {checkingOut ? 'Restoring…' : '⏩ Jump here'}
        </button>
        <button
          onClick={handleFork}
          disabled={forking}
          className="w-full py-2 rounded-lg bg-purple-800 hover:bg-purple-700 disabled:opacity-50 text-white font-medium text-sm"
        >
          {forking ? 'Forking…' : '⑂ Fork here'}
        </button>
      </div>
    </div>
  )
}

// ─── Toast bar ────────────────────────────────────────────────────────────────

function ToastBar({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`flex items-center gap-3 px-4 py-3 rounded-lg shadow-xl text-sm font-medium pointer-events-auto
            ${t.kind === 'success' ? 'bg-green-800 text-green-100' :
              t.kind === 'error'   ? 'bg-red-800 text-red-100' :
                                     'bg-slate-700 text-slate-100'}`}
        >
          <span className="flex-1">{t.message}</span>
          <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100">✕</button>
        </div>
      ))}
    </div>
  )
}

// ─── Legend ───────────────────────────────────────────────────────────────────

function Legend() {
  return (
    <div className="absolute bottom-5 left-5 z-10 bg-slate-900/90 border border-slate-700 rounded-lg px-4 py-3 flex flex-col gap-2 text-xs">
      <div className="flex items-center gap-2">
        <span className="w-5 h-5 rounded flex items-center justify-center bg-amber-900 text-amber-300 font-bold">S</span>
        <span className="text-slate-400">Snapshot — full world state stored</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="w-5 h-5 rounded flex items-center justify-center bg-blue-900 text-blue-300 font-bold">D</span>
        <span className="text-slate-400">Delta — JSON patch from parent</span>
      </div>
      <div className="flex items-center gap-2">
        <span className="w-5 h-5 rounded border-2 border-dashed border-teal-400" />
        <span className="text-slate-400">Branch HEAD</span>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function TimelinePage() {
  const router = useRouter()

  const [allMeta, setAllMeta]         = useState<CommitMeta[]>([])
  const [branches, setBranches]       = useState<Record<string, string>>({})
  const [selectedBranch, setSelectedBranch] = useState<string>('')
  const [selectedCommit, setSelectedCommit] = useState<CommitMeta | null>(null)
  const [loading, setLoading]         = useState(false)
  const [toasts, setToasts]           = useState<Toast[]>([])

  const [nodes, setNodes, onNodesChange] = useNodesState<CommitNodeData>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  // ── Toast helpers ──────────────────────────────────────────────────────────

  const addToast = useCallback((message: string, kind: Toast['kind'] = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(t => [...t, { id, message, kind }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 5000)
  }, [])

  const dismissToast = useCallback((id: string) => setToasts(t => t.filter(x => x.id !== id)), [])

  // ── Data fetching ──────────────────────────────────────────────────────────

  const fetchTimeline = useCallback(async () => {
    setLoading(true)
    try {
      const [timelineRes, branchesRes] = await Promise.all([
        fetch(`${API}/chronos/timeline`),
        fetch(`${API}/chronos/branches`),
      ])
      if (!timelineRes.ok) throw new Error(`Timeline HTTP ${timelineRes.status}`)
      if (!branchesRes.ok) throw new Error(`Branches HTTP ${branchesRes.status}`)

      const meta: CommitMeta[] = await timelineRes.json()
      const branchMap: Record<string, string> = await branchesRes.json()

      setAllMeta(meta)
      setBranches(branchMap)
    } catch (e) {
      addToast(`Failed to load timeline: ${(e as Error).message}`, 'error')
    } finally {
      setLoading(false)
    }
  }, [addToast])

  useEffect(() => { fetchTimeline() }, [fetchTimeline])

  // ── Compute filtered commit list + HEAD set ────────────────────────────────

  const { visibleMeta, headIds } = useMemo(() => {
    const allTipIds = new Set(Object.values(branches))

    if (!selectedBranch) {
      // All branches: show every commit, highlight all branch tips as HEAD.
      return { visibleMeta: allMeta, headIds: allTipIds }
    }

    const tipId = branches[selectedBranch]
    if (!tipId) return { visibleMeta: allMeta, headIds: allTipIds }

    const ancestorSet = ancestorsOf(allMeta, tipId)
    return {
      visibleMeta: allMeta.filter(m => ancestorSet.has(m.commit_id)),
      headIds: new Set([tipId]),
    }
  }, [allMeta, branches, selectedBranch])

  // ── Rebuild React Flow graph when visible commits change ──────────────────

  useEffect(() => {
    if (!visibleMeta.length) { setNodes([]); setEdges([]); return }
    const { nodes: n, edges: e } = toRFElements(visibleMeta, headIds)
    setNodes(n)
    setEdges(e)
  }, [visibleMeta, headIds, setNodes, setEdges])

  // ── Node click ─────────────────────────────────────────────────────────────

  const onNodeClick: NodeMouseHandler = useCallback((_e, rfNode) => {
    setSelectedCommit(rfNode.data as CommitMeta)
  }, [])

  const onPaneClick = useCallback(() => setSelectedCommit(null), [])

  // ── Checkout ───────────────────────────────────────────────────────────────

  const handleCheckout = useCallback(async () => {
    if (!selectedCommit) return
    const res = await fetch(`${API}/chronos/checkout/${selectedCommit.commit_id}`, { method: 'POST' })
    if (!res.ok) throw new Error(await res.text())
    addToast('World state restored — heading to play…', 'success')
    setTimeout(() => router.push('/play'), 1200)
  }, [selectedCommit, router, addToast])

  // ── Fork ───────────────────────────────────────────────────────────────────

  const handleFork = useCallback(async () => {
    if (!selectedCommit) return
    const branchName = globalThis.prompt('New branch name:')?.trim()
    if (!branchName) return
    const res = await fetch(`${API}/chronos/fork`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ from_commit_id: selectedCommit.commit_id, branch_name: branchName }),
    })
    if (!res.ok) throw new Error(await res.text())
    addToast(`Branch "${branchName}" created at ${selectedCommit.commit_id.slice(0, 8)}.`, 'success')
    setSelectedCommit(null)
    fetchTimeline()
  }, [selectedCommit, addToast, fetchTimeline])

  // ── Prune ──────────────────────────────────────────────────────────────────

  const handlePrune = useCallback(async () => {
    try {
      const res = await fetch(`${API}/chronos/prune`, { method: 'DELETE' })
      if (!res.ok) throw new Error(await res.text())
      const { deleted_objects } = await res.json()
      addToast(
        deleted_objects > 0
          ? `Pruned ${deleted_objects} orphaned commit object(s).`
          : 'No orphaned commits found.',
        deleted_objects > 0 ? 'success' : 'info',
      )
      fetchTimeline()
    } catch (e) {
      addToast(`Prune failed: ${(e as Error).message}`, 'error')
    }
  }, [addToast, fetchTimeline])

  // ── Render ─────────────────────────────────────────────────────────────────

  const headCommit = selectedBranch ? branches[selectedBranch] : null

  return (
    <div className="w-full h-screen bg-slate-950 flex flex-col">
      {/* Top bar */}
      <div className="flex items-center gap-3 px-5 py-3 bg-slate-900 border-b border-slate-700 shrink-0 z-10">
        <h1 className="text-white font-bold tracking-tight">Chronos Timeline</h1>

        {/* Branch selector */}
        <select
          value={selectedBranch}
          onChange={e => { setSelectedBranch(e.target.value); setSelectedCommit(null) }}
          className="bg-slate-800 border border-slate-600 text-white text-sm rounded px-2 py-1.5 focus:border-teal-400 outline-none"
        >
          <option value="">All branches ({allMeta.length} commits)</option>
          {Object.entries(branches).map(([name, tip]) => (
            <option key={name} value={name}>
              {name} ({tip.slice(0, 8)})
            </option>
          ))}
        </select>

        {headCommit && (
          <span className="text-teal-400 font-mono text-xs bg-teal-900/30 px-2 py-1 rounded">
            HEAD: {headCommit.slice(0, 10)}…
          </span>
        )}

        {loading && <span className="text-slate-500 text-sm">Loading…</span>}

        <div className="flex-1" />

        <button
          onClick={() => fetchTimeline()}
          className="px-3 py-1.5 rounded bg-slate-700 hover:bg-slate-600 text-slate-300 text-sm"
        >
          ↺ Refresh
        </button>
        <button
          onClick={handlePrune}
          className="px-3 py-1.5 rounded bg-red-900 hover:bg-red-800 text-red-300 text-sm font-medium"
        >
          🗑 Prune orphans
        </button>
      </div>

      {/* Graph area */}
      <div className="flex-1 relative">
        {visibleMeta.length === 0 && !loading ? (
          <div className="absolute inset-0 flex flex-col items-center justify-center text-slate-600 gap-3">
            <span className="text-5xl">⑂</span>
            <p className="text-lg font-medium">No commits yet</p>
            <p className="text-sm">Play a scene to start the timeline.</p>
          </div>
        ) : (
          <ReactFlow
            nodes={nodes}
            edges={edges}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onNodeClick={onNodeClick}
            onPaneClick={onPaneClick}
            nodeTypes={nodeTypes}
            nodesDraggable={false}
            fitView
            fitViewOptions={{ padding: 0.2 }}
            minZoom={0.05}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
          >
            <Controls className="!bg-slate-800 !border-slate-700" />
            <Background variant={BackgroundVariant.Dots} color="#1E293B" gap={24} size={1} />
          </ReactFlow>
        )}

        {/* Legend */}
        <Legend />
      </div>

      {/* Commit detail panel */}
      {selectedCommit && (
        <CommitPanel
          commit={selectedCommit}
          onClose={() => setSelectedCommit(null)}
          onCheckout={handleCheckout}
          onFork={handleFork}
        />
      )}

      <ToastBar toasts={toasts} dismiss={dismissToast} />
    </div>
  )
}
