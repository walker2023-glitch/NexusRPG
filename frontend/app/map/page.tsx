/**
 * Knowledge Graph Map — React Flow visualisation of the Nexus world state.
 *
 * Required packages (add to frontend/package.json):
 *   reactflow, dagre, @types/dagre
 */
'use client'

import React, {
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
} from 'react'
import ReactFlow, {
  Node,
  Edge,
  Controls,
  MiniMap,
  Background,
  BackgroundVariant,
  useNodesState,
  useEdgesState,
  Handle,
  Position,
  type NodeProps,
  type EdgeMouseHandler,
  type NodeMouseHandler,
} from 'reactflow'
import 'reactflow/dist/style.css'

// ─── Constants ────────────────────────────────────────────────────────────────

const API = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')

const TYPE_COLOR: Record<string, string> = {
  char:    '#3B82F6',   // blue
  loc:     '#22C55E',   // green
  item:    '#F59E0B',   // amber
  org:     '#8B5CF6',   // purple
  event:   '#EF4444',   // red
  concept: '#6B7280',   // gray
}

const TYPE_LABEL: Record<string, string> = {
  char: 'Character', loc: 'Location', item: 'Item',
  org: 'Organization', event: 'Event', concept: 'Concept',
}

const ENTITY_TYPES = ['char', 'loc', 'item', 'org', 'event', 'concept'] as const
type EntityType = typeof ENTITY_TYPES[number]

const NODE_W = 180
const NODE_H = 64
const POLL_MS = 2000

// ─── Types ────────────────────────────────────────────────────────────────────

interface WorldNode {
  id: string
  label: string
  entity_type: string
  description?: string
  properties?: Record<string, unknown>
}

interface WorldEdge {
  source: string
  target: string
  relation: string
}

interface WorldState {
  nodes: WorldNode[]
  edges: WorldEdge[]
  metadata: Record<string, unknown>
}

interface Toast {
  id: string
  message: string
  kind: 'success' | 'error' | 'info'
}

interface NodeData extends WorldNode {
  color: string
}

// ─── Dagre layout ─────────────────────────────────────────────────────────────

function applyDagre(nodes: Node[], edges: Edge[]): { nodes: Node[]; edges: Edge[] } {
  // Dynamic require keeps this tree-shakeable and avoids SSR issues.
  // eslint-disable-next-line @typescript-eslint/no-var-requires
  const dagre = require('dagre') as typeof import('dagre')
  const g = new dagre.graphlib.Graph()
  g.setDefaultEdgeLabel(() => ({}))
  g.setGraph({ rankdir: 'TB', ranksep: 90, nodesep: 60 })
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

// ─── World state → React Flow transform ──────────────────────────────────────

function toRFElements(ws: WorldState): { nodes: Node<NodeData>[]; edges: Edge[] } {
  const rawNodes: Node<NodeData>[] = ws.nodes.map((n, i) => ({
    id: n.id,
    type: 'entityNode',
    position: { x: (i % 5) * 220, y: Math.floor(i / 5) * 120 }, // fallback grid
    data: { ...n, color: TYPE_COLOR[n.entity_type] ?? '#6B7280' },
  }))

  const rawEdges: Edge[] = ws.edges.map(e => ({
    id: `${e.source}__${e.relation}__${e.target}`,
    source: e.source,
    target: e.target,
    label: e.relation.replace(/_/g, ' '),
    labelStyle: { fontSize: 11, fill: '#94A3B8' },
    labelBgStyle: { fill: '#1E293B', fillOpacity: 0.8 },
    style: { stroke: '#475569', strokeWidth: 1.5 },
    animated: false,
  }))

  return applyDagre(rawNodes, rawEdges) as { nodes: Node<NodeData>[]; edges: Edge[] }
}

// ─── Custom node ──────────────────────────────────────────────────────────────

function EntityNode({ data }: NodeProps<NodeData>) {
  const color = data.color ?? '#6B7280'
  const shortType = data.entity_type ?? 'concept'
  return (
    <div
      className="rounded-lg bg-slate-800 text-white shadow-lg select-none"
      style={{ border: `2px solid ${color}`, minWidth: NODE_W, minHeight: NODE_H }}
    >
      <Handle type="target" position={Position.Top} style={{ background: color }} />
      <div className="px-3 py-2">
        <p className="text-sm font-semibold leading-tight truncate">{data.label}</p>
        <span
          className="mt-1 inline-block px-1.5 py-0.5 rounded text-[10px] font-mono uppercase tracking-wide"
          style={{ background: color + '33', color }}
        >
          {TYPE_LABEL[shortType] ?? shortType}
        </span>
      </div>
      <Handle type="source" position={Position.Bottom} style={{ background: color }} />
    </div>
  )
}

const nodeTypes = { entityNode: EntityNode }

// ─── Toast component ──────────────────────────────────────────────────────────

function ToastBar({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div
          key={t.id}
          className={`flex items-center gap-3 px-4 py-3 rounded-lg shadow-xl text-sm font-medium pointer-events-auto
            ${t.kind === 'success' ? 'bg-green-700 text-green-100' :
              t.kind === 'error'   ? 'bg-red-700 text-red-100' :
                                     'bg-slate-700 text-slate-100'}`}
        >
          <span className="flex-1">{t.message}</span>
          <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100">✕</button>
        </div>
      ))}
    </div>
  )
}

// ─── Edge info panel ──────────────────────────────────────────────────────────

function EdgePanel({
  edge,
  onClose,
  onDelete,
}: { edge: Edge; onClose: () => void; onDelete: () => void }) {
  return (
    <div className="fixed bottom-5 left-1/2 -translate-x-1/2 z-40 bg-slate-800 border border-slate-600 rounded-lg shadow-xl px-5 py-3 flex items-center gap-4 text-sm">
      <span className="text-slate-400">Edge relation:</span>
      <span className="font-mono text-amber-400">{String(edge.label)}</span>
      <span className="text-slate-500 text-xs">({edge.source} → {edge.target})</span>
      <button
        onClick={onDelete}
        className="ml-2 px-3 py-1 rounded bg-red-700 hover:bg-red-600 text-red-100 text-xs"
      >
        Delete
      </button>
      <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
    </div>
  )
}

// ─── Drawer ───────────────────────────────────────────────────────────────────

function NodeDrawer({
  node,
  onClose,
  onSaved,
  addToast,
}: {
  node: WorldNode
  onClose: () => void
  onSaved: () => void
  addToast: (m: string, k: Toast['kind']) => void
}) {
  const [props, setProps] = useState<Record<string, string>>(
    Object.fromEntries(
      Object.entries(node.properties ?? {}).map(([k, v]) => [k, String(v)])
    )
  )
  const [newKey, setNewKey] = useState('')
  const [newVal, setNewVal] = useState('')
  const [saving, setSaving] = useState(false)

  const save = useCallback(async () => {
    setSaving(true)
    try {
      const res = await fetch(`${API}/api/nodes/${node.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ properties: props }),
      })
      if (!res.ok) throw new Error(await res.text())
      addToast(`Node "${node.label}" saved.`, 'success')
      onSaved()
    } catch (e) {
      addToast(`Save failed: ${(e as Error).message}`, 'error')
    } finally {
      setSaving(false)
    }
  }, [node, props, addToast, onSaved])

  const addProp = () => {
    if (!newKey.trim()) return
    setProps(p => ({ ...p, [newKey.trim()]: newVal }))
    setNewKey('')
    setNewVal('')
  }

  const color = TYPE_COLOR[node.entity_type] ?? '#6B7280'

  return (
    <div className="fixed top-0 right-0 h-full w-80 bg-slate-900 border-l border-slate-700 shadow-2xl z-30 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-slate-700">
        <div>
          <p className="text-white font-semibold truncate">{node.label}</p>
          <span
            className="text-xs font-mono px-1.5 py-0.5 rounded"
            style={{ background: color + '33', color }}
          >
            {node.id}
          </span>
        </div>
        <button onClick={onClose} className="text-slate-400 hover:text-white text-lg">✕</button>
      </div>

      {/* Description */}
      {node.description && (
        <p className="px-4 py-2 text-sm text-slate-300 italic border-b border-slate-800">
          {node.description}
        </p>
      )}

      {/* Properties */}
      <div className="flex-1 overflow-y-auto px-4 py-3 space-y-2">
        <p className="text-xs uppercase tracking-widest text-slate-500 mb-2">Properties</p>
        {Object.entries(props).map(([k, v]) => (
          <div key={k} className="flex gap-2 items-center">
            <span className="text-xs text-slate-400 w-28 shrink-0 font-mono truncate">{k}</span>
            <input
              value={v}
              onChange={e => setProps(p => ({ ...p, [k]: e.target.value }))}
              className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:border-blue-500 outline-none"
            />
            <button
              onClick={() => setProps(p => { const n = { ...p }; delete n[k]; return n })}
              className="text-slate-500 hover:text-red-400 text-xs"
            >✕</button>
          </div>
        ))}

        {/* Add property row */}
        <div className="flex gap-2 items-center pt-2 border-t border-slate-800">
          <input
            placeholder="key"
            value={newKey}
            onChange={e => setNewKey(e.target.value)}
            className="w-24 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:border-blue-500 outline-none"
          />
          <input
            placeholder="value"
            value={newVal}
            onChange={e => setNewVal(e.target.value)}
            className="flex-1 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-sm text-white focus:border-blue-500 outline-none"
          />
          <button
            onClick={addProp}
            className="px-2 py-1 rounded bg-blue-700 hover:bg-blue-600 text-white text-sm"
          >+</button>
        </div>
      </div>

      {/* Save */}
      <div className="px-4 py-3 border-t border-slate-700">
        <button
          onClick={save}
          disabled={saving}
          className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium text-sm"
        >
          {saving ? 'Saving…' : 'Save changes'}
        </button>
      </div>
    </div>
  )
}

// ─── Add Node Modal ───────────────────────────────────────────────────────────

function AddNodeModal({
  onClose,
  onCreated,
  addToast,
}: { onClose: () => void; onCreated: () => void; addToast: (m: string, k: Toast['kind']) => void }) {
  const [type, setType] = useState<EntityType>('char')
  const [label, setLabel] = useState('')
  const [desc, setDesc] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!label.trim()) return
    setBusy(true)
    const text = `${TYPE_LABEL[type]}: ${label.trim()}. ${desc.trim()}`
    try {
      const res = await fetch(`${API}/architect/extract`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ raw_text: text }),
      })
      if (!res.ok) throw new Error(await res.text())
      addToast(`Node "${label}" queued for extraction.`, 'success')
      onCreated()
      onClose()
    } catch (e) {
      addToast(`Failed: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="Add Node" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-slate-400">Entity type</label>
          <div className="flex flex-wrap gap-2 mt-1">
            {ENTITY_TYPES.map(t => (
              <button
                key={t}
                onClick={() => setType(t)}
                className="px-3 py-1 rounded-full text-xs font-mono border"
                style={{
                  borderColor: TYPE_COLOR[t],
                  background: type === t ? TYPE_COLOR[t] + '33' : 'transparent',
                  color: TYPE_COLOR[t],
                }}
              >
                {TYPE_LABEL[t]}
              </button>
            ))}
          </div>
        </div>
        <div>
          <label className="text-xs text-slate-400">Label</label>
          <input
            autoFocus
            value={label}
            onChange={e => setLabel(e.target.value)}
            placeholder="Display name…"
            className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none"
          />
        </div>
        <div>
          <label className="text-xs text-slate-400">Description (optional)</label>
          <textarea
            value={desc}
            onChange={e => setDesc(e.target.value)}
            rows={2}
            placeholder="Brief factual description…"
            className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none resize-none"
          />
        </div>
        <button
          onClick={submit}
          disabled={busy || !label.trim()}
          className="w-full py-2 rounded-lg bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white font-medium text-sm"
        >
          {busy ? 'Extracting…' : 'Create node'}
        </button>
      </div>
    </ModalShell>
  )
}

// ─── Scrape Modal ─────────────────────────────────────────────────────────────

function ScrapeModal({
  onClose,
  onDone,
  addToast,
}: { onClose: () => void; onDone: () => void; addToast: (m: string, k: Toast['kind']) => void }) {
  const [url, setUrl] = useState('')
  const [busy, setBusy] = useState(false)

  const submit = async () => {
    if (!url.trim()) return
    setBusy(true)
    try {
      const res = await fetch(`${API}/architect/scrape`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ url: url.trim() }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()
      addToast(`Scraped ${data.new_nodes} nodes, ${data.new_edges} edges.`, 'success')
      onDone()
      onClose()
    } catch (e) {
      addToast(`Scrape failed: ${(e as Error).message}`, 'error')
    } finally {
      setBusy(false)
    }
  }

  return (
    <ModalShell title="Scrape URL" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-slate-400">URL to crawl</label>
          <input
            autoFocus
            value={url}
            onChange={e => setUrl(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && submit()}
            placeholder="https://…"
            className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none"
          />
        </div>
        <p className="text-xs text-slate-500">
          Page markdown is truncated to 6 000 chars before extraction.
        </p>
        <button
          onClick={submit}
          disabled={busy || !url.trim()}
          className="w-full py-2 rounded-lg bg-green-700 hover:bg-green-600 disabled:opacity-50 text-white font-medium text-sm"
        >
          {busy ? 'Scraping…' : 'Scrape & extract'}
        </button>
      </div>
    </ModalShell>
  )
}

// ─── Import Modal ─────────────────────────────────────────────────────────────

function ImportModal({
  onClose,
  addToast,
  onJobStarted,
}: { onClose: () => void; addToast: (m: string, k: Toast['kind']) => void; onJobStarted: (id: string) => void }) {
  const [dragOver, setDragOver] = useState(false)
  const [entityId, setEntityId] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    const isImage = file.type.startsWith('image/')
    const formData = new FormData()
    formData.append('file', file)

    let url = `${API}/import/text`
    if (isImage) {
      if (!entityId.trim()) {
        addToast('Enter an entity ID for image import.', 'error')
        return
      }
      formData.append('entity_id', entityId.trim())
      url = `${API}/import/image`
    }

    try {
      const res = await fetch(url, { method: 'POST', body: formData })
      if (!res.ok) throw new Error(await res.text())
      const { job_id } = await res.json()
      addToast(`Import queued (job ${job_id.slice(0, 8)}…)`, 'info')
      onJobStarted(job_id)
      onClose()
    } catch (e) {
      addToast(`Import failed: ${(e as Error).message}`, 'error')
    }
  }, [entityId, addToast, onClose, onJobStarted])

  const onDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault()
    setDragOver(false)
    const file = e.dataTransfer.files[0]
    if (file) handleFile(file)
  }, [handleFile])

  return (
    <ModalShell title="Import File" onClose={onClose}>
      <div className="space-y-3">
        <div>
          <label className="text-xs text-slate-400">Entity ID (required for images)</label>
          <input
            value={entityId}
            onChange={e => setEntityId(e.target.value)}
            placeholder="char_theron"
            className="mt-1 w-full bg-slate-800 border border-slate-700 rounded px-3 py-2 text-sm text-white focus:border-blue-500 outline-none font-mono"
          />
        </div>

        <div
          onDragOver={e => { e.preventDefault(); setDragOver(true) }}
          onDragLeave={() => setDragOver(false)}
          onDrop={onDrop}
          onClick={() => inputRef.current?.click()}
          className={`border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
            ${dragOver ? 'border-blue-400 bg-blue-900/20' : 'border-slate-600 hover:border-slate-400'}`}
        >
          <p className="text-slate-400 text-sm">
            Drop a <span className="text-white">.md / .txt</span> or{' '}
            <span className="text-white">.png / .jpg</span> here<br />
            <span className="text-xs text-slate-500">or click to browse</span>
          </p>
          <input
            ref={inputRef}
            type="file"
            accept=".md,.txt,.png,.jpg,.jpeg,.webp"
            className="hidden"
            onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }}
          />
        </div>
      </div>
    </ModalShell>
  )
}

// ─── Modal shell ──────────────────────────────────────────────────────────────

function ModalShell({ title, onClose, children }: { title: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="fixed inset-0 bg-black/60 z-50 flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-md">
        <div className="flex items-center justify-between px-5 py-4 border-b border-slate-700">
          <h2 className="text-white font-semibold">{title}</h2>
          <button onClick={onClose} className="text-slate-400 hover:text-white">✕</button>
        </div>
        <div className="px-5 py-4">{children}</div>
      </div>
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function MapPage() {
  const [nodes, setNodes, onNodesChange] = useNodesState<NodeData>([])
  const [edges, setEdges, onEdgesChange] = useEdgesState([])

  const [selectedNode, setSelectedNode] = useState<WorldNode | null>(null)
  const [selectedEdge, setSelectedEdge] = useState<Edge | null>(null)
  const [toasts, setToasts] = useState<Toast[]>([])
  const [loading, setLoading] = useState(false)

  const [showAdd, setShowAdd] = useState(false)
  const [showScrape, setShowScrape] = useState(false)
  const [showImport, setShowImport] = useState(false)

  const [jobId, setJobId] = useState<string | null>(null)
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Toast helpers ──────────────────────────────────────────────────────────

  const addToast = useCallback((message: string, kind: Toast['kind'] = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(t => [...t, { id, message, kind }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 5000)
  }, [])

  const dismissToast = useCallback((id: string) => {
    setToasts(t => t.filter(x => x.id !== id))
  }, [])

  // ── World state fetch ──────────────────────────────────────────────────────

  const fetchGraph = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API}/api/world-state`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const ws: WorldState = await res.json()
      const { nodes: n, edges: e } = toRFElements(ws)
      setNodes(n)
      setEdges(e)
    } catch (err) {
      addToast(`Failed to load world state: ${(err as Error).message}`, 'error')
    } finally {
      setLoading(false)
    }
  }, [setNodes, setEdges, addToast])

  useEffect(() => { fetchGraph() }, [fetchGraph])

  // ── Import job polling ─────────────────────────────────────────────────────

  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/import/status/${jobId}`)
        if (!res.ok) return
        const data = await res.json()
        if (data.status === 'complete') {
          addToast(`Import complete — ${data.result?.chunks ?? 1} chunk(s) indexed.`, 'success')
          setJobId(null)
          fetchGraph()
        } else if (data.status === 'error') {
          addToast(`Import error: ${data.error}`, 'error')
          setJobId(null)
        }
      } catch { /* network glitch, keep polling */ }
    }, POLL_MS)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [jobId, addToast, fetchGraph])

  // ── Node / edge click handlers ─────────────────────────────────────────────

  const onNodeClick: NodeMouseHandler = useCallback((_e, node) => {
    setSelectedEdge(null)
    setSelectedNode((node.data as NodeData) as WorldNode)
  }, [])

  const onEdgeClick: EdgeMouseHandler = useCallback((_e, edge) => {
    setSelectedNode(null)
    setSelectedEdge(edge)
  }, [])

  const onPaneClick = useCallback(() => {
    setSelectedNode(null)
    setSelectedEdge(null)
  }, [])

  // ── Delete edge ────────────────────────────────────────────────────────────

  const deleteEdge = useCallback(async () => {
    if (!selectedEdge) return
    try {
      await fetch(
        `${API}/api/edges?source=${selectedEdge.source}&target=${selectedEdge.target}&relation=${encodeURIComponent(String(selectedEdge.label))}`,
        { method: 'DELETE' }
      )
      setSelectedEdge(null)
      fetchGraph()
    } catch (e) {
      addToast(`Delete failed: ${(e as Error).message}`, 'error')
    }
  }, [selectedEdge, fetchGraph, addToast])

  // ── Toolbar ────────────────────────────────────────────────────────────────

  const Toolbar = (
    <div className="absolute top-4 left-4 z-10 flex gap-2">
      <button
        onClick={() => setShowAdd(true)}
        className="px-3 py-2 rounded-lg bg-blue-700 hover:bg-blue-600 text-white text-sm font-medium shadow"
      >
        + Add node
      </button>
      <button
        onClick={() => setShowScrape(true)}
        className="px-3 py-2 rounded-lg bg-green-700 hover:bg-green-600 text-white text-sm font-medium shadow"
      >
        🌐 Scrape URL
      </button>
      <button
        onClick={() => setShowImport(true)}
        className="px-3 py-2 rounded-lg bg-amber-700 hover:bg-amber-600 text-white text-sm font-medium shadow"
      >
        ↑ Import file
      </button>
      {loading && (
        <span className="px-3 py-2 text-slate-400 text-sm">Loading…</span>
      )}
      {jobId && (
        <span className="px-3 py-2 rounded-lg bg-slate-700 text-slate-300 text-sm animate-pulse">
          Importing…
        </span>
      )}
    </div>
  )

  return (
    <div className="w-full h-screen bg-slate-950 relative">
      {Toolbar}

      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick}
        onEdgeClick={onEdgeClick}
        onPaneClick={onPaneClick}
        nodeTypes={nodeTypes}
        fitView
        fitViewOptions={{ padding: 0.15 }}
        minZoom={0.1}
        maxZoom={3}
        proOptions={{ hideAttribution: true }}
      >
        <Controls className="!bg-slate-800 !border-slate-700 !shadow-lg" />
        <MiniMap
          nodeColor={(n) => (n.data as NodeData)?.color ?? '#6B7280'}
          maskColor="rgba(15,23,42,0.85)"
          className="!bg-slate-900 !border-slate-700"
        />
        <Background variant={BackgroundVariant.Dots} color="#334155" gap={20} size={1} />
      </ReactFlow>

      {/* Right-side node drawer */}
      {selectedNode && (
        <NodeDrawer
          node={selectedNode}
          onClose={() => setSelectedNode(null)}
          onSaved={fetchGraph}
          addToast={addToast}
        />
      )}

      {/* Edge info bar */}
      {selectedEdge && (
        <EdgePanel
          edge={selectedEdge}
          onClose={() => setSelectedEdge(null)}
          onDelete={deleteEdge}
        />
      )}

      {/* Modals */}
      {showAdd    && <AddNodeModal    onClose={() => setShowAdd(false)}    onCreated={fetchGraph}                  addToast={addToast} />}
      {showScrape && <ScrapeModal     onClose={() => setShowScrape(false)} onDone={fetchGraph}                     addToast={addToast} />}
      {showImport && <ImportModal     onClose={() => setShowImport(false)} onJobStarted={setJobId}                 addToast={addToast} />}

      <ToastBar toasts={toasts} dismiss={dismissToast} />
    </div>
  )
}
