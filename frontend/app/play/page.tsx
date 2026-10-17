/**
 * Play — main Actor-mode game interface.
 *
 * Required packages: swr  (reactflow + dagre already in package.json from /map)
 *
 * Layout (grid-cols-[1fr_256px]):
 *   [top bar spanning full width]
 *   [story feed flex-1] | [Freytag pyramid + import panel w-64]
 *   [choice bar + custom input spanning story column]
 */
'use client'

import React, {
  useState,
  useCallback,
  useEffect,
  useRef,
  useMemo,
} from 'react'
import Link from 'next/link'
import { useRouter } from 'next/navigation'
import useSWR, { mutate } from 'swr'

// ─── Constants ────────────────────────────────────────────────────────────────

const API = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
const POLL_MS = 2500
const STREAM_CHUNK = 14   // characters revealed per tick
const STREAM_TICK_MS = 35 // ms between ticks → ~400 chars/s

// ─── Types ────────────────────────────────────────────────────────────────────

type FreytagStageId =
  | 'EXPOSITION'
  | 'RISING_ACTION'
  | 'CLIMAX'
  | 'FALLING_ACTION'
  | 'RESOLUTION'

interface StageConfig {
  id: FreytagStageId
  label: string
  temp: number
  color: string
  description: string
}

interface Choices { A: string; B: string; C: string }

interface SceneEntry {
  scene_text: string
  choices: Choices
  commit_id: string
  active_location: string | null
  axiom_violations: string[]
  branch_label: string | null
  timestamp: number
  choice_taken?: string
}

interface WorldNode {
  id: string
  label: string
  entity_type: string
  properties?: Record<string, unknown>
}

interface WorldState {
  nodes: WorldNode[]
  edges: unknown[]
  metadata: { universe?: string; active_location?: string | null; session_id?: string | null }
}

interface Toast {
  id: string
  message: string
  kind: 'success' | 'error' | 'info'
}

// ─── Freytag stages config ────────────────────────────────────────────────────

const STAGES: StageConfig[] = [
  {
    id: 'EXPOSITION',
    label: 'Exposition',
    temp: 0.65,
    color: '#3B82F6',
    description: 'Slow world-building, long sentences',
  },
  {
    id: 'RISING_ACTION',
    label: 'Rising Action',
    temp: 0.75,
    color: '#8B5CF6',
    description: 'Tension climbs, stakes clarify',
  },
  {
    id: 'CLIMAX',
    label: 'Climax',
    temp: 0.88,
    color: '#EF4444',
    description: 'Short sentences, sensory overload',
  },
  {
    id: 'FALLING_ACTION',
    label: 'Falling Action',
    temp: 0.70,
    color: '#F59E0B',
    description: 'Aftermath, consequences arrive',
  },
  {
    id: 'RESOLUTION',
    label: 'Resolution',
    temp: 0.60,
    color: '#22C55E',
    description: 'Threads close, reflection',
  },
]

// Normalize temperature to a 60–100% bar width for the pyramid visual
const TEMP_MIN = 0.60
const TEMP_MAX = 0.88
function tempToWidth(temp: number): string {
  const pct = ((temp - TEMP_MIN) / (TEMP_MAX - TEMP_MIN)) * 40 + 60
  return `${pct.toFixed(1)}%`
}

// ─── SWR fetcher ─────────────────────────────────────────────────────────────

const fetcher = (url: string) => fetch(url).then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json() })

// ─── Typewriter hook ──────────────────────────────────────────────────────────

function useTypewriter(target: string): { displayed: string; done: boolean } {
  const [displayed, setDisplayed] = useState('')
  const [done, setDone] = useState(true)
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null)

  useEffect(() => {
    if (!target) { setDisplayed(''); setDone(true); return }
    setDisplayed('')
    setDone(false)
    let i = 0
    timerRef.current = setInterval(() => {
      i = Math.min(i + STREAM_CHUNK, target.length)
      setDisplayed(target.slice(0, i))
      if (i >= target.length) {
        if (timerRef.current) clearInterval(timerRef.current)
        setDone(true)
      }
    }, STREAM_TICK_MS)
    return () => { if (timerRef.current) clearInterval(timerRef.current) }
  }, [target])

  return { displayed, done }
}

// ─── Sub-components ───────────────────────────────────────────────────────────

// Freytag Pyramid sidebar
function FreytagPanel({
  active,
  onChange,
}: { active: FreytagStageId; onChange: (s: FreytagStageId) => void }) {
  return (
    <div className="flex flex-col px-3 py-4 gap-2">
      <p className="text-xs uppercase tracking-widest text-slate-500 mb-1">Narrative Stage</p>
      {STAGES.map(s => {
        const isActive = s.id === active
        return (
          <button
            key={s.id}
            onClick={() => onChange(s.id)}
            className="flex flex-col items-center gap-1 group"
          >
            {/* Bar — width proportional to temperature (pyramid shape) */}
            <div
              className="rounded transition-all duration-200 flex items-center justify-between px-2.5 py-1.5"
              style={{
                width: tempToWidth(s.temp),
                background: isActive ? s.color : s.color + '22',
                border: `1px solid ${isActive ? s.color : s.color + '44'}`,
                opacity: isActive ? 1 : 0.65,
              }}
            >
              <span
                className="text-xs font-semibold truncate"
                style={{ color: isActive ? '#fff' : s.color }}
              >
                {s.label}
              </span>
              {isActive && (
                <span className="text-[10px] text-white/80 font-mono shrink-0">
                  {s.temp}
                </span>
              )}
            </div>

            {isActive && (
              <p className="text-[10px] text-slate-500 leading-tight px-1 text-center">
                {s.description}
              </p>
            )}
          </button>
        )
      })}
    </div>
  )
}

// Individual scene card
function SceneCard({
  entry,
  isLatest,
  displayedText,
  typingDone,
}: {
  entry: SceneEntry
  isLatest: boolean
  displayedText: string
  typingDone: boolean
}) {
  const text = isLatest ? displayedText : entry.scene_text

  return (
    <div className={`rounded-xl p-4 space-y-3 transition-all ${isLatest ? 'bg-slate-800/80 border border-slate-600' : 'bg-slate-900/60 border border-slate-800'}`}>
      {/* Meta row */}
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-[10px] text-slate-500 font-mono">
          {new Date(entry.timestamp).toLocaleTimeString(undefined, { timeStyle: 'short' })}
        </span>
        {entry.active_location && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-green-900/50 text-green-400 font-mono">
            📍 {entry.active_location.replace('loc_', '')}
          </span>
        )}
        {entry.branch_label && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-teal-900/50 text-teal-400 font-mono">
            ⑂ {entry.branch_label}
          </span>
        )}
        {entry.choice_taken && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-purple-900/50 text-purple-300">
            → chose {entry.choice_taken}
          </span>
        )}
        {entry.axiom_violations.length > 0 && (
          <span className="px-1.5 py-0.5 rounded text-[10px] bg-red-900/50 text-red-400" title={entry.axiom_violations.join('; ')}>
            ⚠ {entry.axiom_violations.length} axiom fix{entry.axiom_violations.length > 1 ? 'es' : ''}
          </span>
        )}
      </div>

      {/* Scene text */}
      <p className="text-slate-200 leading-relaxed text-sm whitespace-pre-wrap">
        {text}
        {isLatest && !typingDone && (
          <span className="inline-block w-0.5 h-4 bg-blue-400 ml-0.5 animate-pulse align-middle" />
        )}
      </p>
    </div>
  )
}

// Location selector popover
function LocationSelector({
  nodes,
  current,
  onSelect,
  onClose,
}: { nodes: WorldNode[]; current: string | null; onSelect: (id: string) => void; onClose: () => void }) {
  const locs = useMemo(() => nodes.filter(n => n.entity_type === 'loc'), [nodes])
  return (
    <div className="absolute top-full left-0 mt-1 z-50 bg-slate-800 border border-slate-600 rounded-lg shadow-2xl min-w-[200px] max-h-64 overflow-y-auto">
      {locs.length === 0 ? (
        <p className="px-4 py-3 text-sm text-slate-500 italic">No locations in world state yet.</p>
      ) : locs.map(n => (
        <button
          key={n.id}
          onClick={() => { onSelect(n.id); onClose() }}
          className={`w-full text-left px-4 py-2 text-sm hover:bg-slate-700 transition-colors
            ${current === n.id ? 'text-green-400 font-medium' : 'text-slate-300'}`}
        >
          {n.label}
          <span className="ml-2 text-[10px] text-slate-500 font-mono">{n.id}</span>
        </button>
      ))}
    </div>
  )
}

// Collapsible import panel
function ImportPanel({
  addToast,
  onJobStarted,
}: { addToast: (m: string, k: Toast['kind']) => void; onJobStarted: (id: string) => void }) {
  const [open, setOpen] = useState(false)
  const [dragOver, setDragOver] = useState(false)
  const [entityId, setEntityId] = useState('')
  const inputRef = useRef<HTMLInputElement>(null)

  const handleFile = useCallback(async (file: File) => {
    const isImage = file.type.startsWith('image/')
    const formData = new FormData()
    formData.append('file', file)
    if (isImage) {
      if (!entityId.trim()) { addToast('Enter an entity ID for image import.', 'error'); return }
      formData.append('entity_id', entityId.trim())
    }
    const url = `${API}/import/${isImage ? 'image' : 'text'}`
    try {
      const res = await fetch(url, { method: 'POST', body: formData })
      if (!res.ok) throw new Error(await res.text())
      const { job_id } = await res.json()
      addToast(`Import queued (${job_id.slice(0, 8)}…)`, 'info')
      onJobStarted(job_id)
    } catch (e) { addToast(`Import failed: ${(e as Error).message}`, 'error') }
  }, [entityId, addToast, onJobStarted])

  return (
    <div className="border-t border-slate-700">
      <button
        onClick={() => setOpen(o => !o)}
        className="w-full px-3 py-2 flex items-center justify-between text-xs text-slate-400 hover:text-white hover:bg-slate-800/50 transition-colors"
      >
        <span>↑ Import assets</span>
        <span>{open ? '▾' : '▸'}</span>
      </button>

      {open && (
        <div className="px-3 pb-3 space-y-2">
          <input
            placeholder="entity_id for images"
            value={entityId}
            onChange={e => setEntityId(e.target.value)}
            className="w-full bg-slate-800 border border-slate-700 rounded px-2 py-1 text-xs text-white font-mono focus:border-blue-500 outline-none"
          />
          <div
            onDragOver={e => { e.preventDefault(); setDragOver(true) }}
            onDragLeave={() => setDragOver(false)}
            onDrop={e => { e.preventDefault(); setDragOver(false); const f = e.dataTransfer.files[0]; if (f) handleFile(f) }}
            onClick={() => inputRef.current?.click()}
            className={`border border-dashed rounded p-4 text-center cursor-pointer text-xs transition-colors
              ${dragOver ? 'border-blue-400 bg-blue-900/20 text-blue-300' : 'border-slate-600 text-slate-500 hover:border-slate-400'}`}
          >
            Drop .md / .txt / .png / .jpg
            <input ref={inputRef} type="file" accept=".md,.txt,.png,.jpg,.jpeg,.webp" className="hidden"
              onChange={e => { const f = e.target.files?.[0]; if (f) handleFile(f) }} />
          </div>
        </div>
      )}
    </div>
  )
}

// Toast bar
function ToastBar({ toasts, dismiss }: { toasts: Toast[]; dismiss: (id: string) => void }) {
  if (!toasts.length) return null
  return (
    <div className="fixed bottom-5 right-5 z-50 flex flex-col gap-2 pointer-events-none">
      {toasts.map(t => (
        <div key={t.id} className={`flex items-center gap-3 px-4 py-3 rounded-lg shadow-xl text-sm font-medium pointer-events-auto
          ${t.kind === 'success' ? 'bg-green-800 text-green-100' : t.kind === 'error' ? 'bg-red-800 text-red-100' : 'bg-slate-700 text-slate-100'}`}>
          <span className="flex-1">{t.message}</span>
          <button onClick={() => dismiss(t.id)} className="opacity-60 hover:opacity-100">✕</button>
        </div>
      ))}
    </div>
  )
}

// ─── Main page ────────────────────────────────────────────────────────────────

export default function PlayPage() {
  const router = useRouter()

  // ── World state (SWR, refreshes after each scene) ─────────────────────────
  const { data: worldState, mutate: refreshWorld } = useSWR<WorldState>(
    `${API}/api/world-state`,
    fetcher,
    { refreshInterval: 0, revalidateOnFocus: false },
  )

  // ── Scene history ─────────────────────────────────────────────────────────
  const [scenes, setScenes]               = useState<SceneEntry[]>([])
  const [stage, setStage]                 = useState<FreytagStageId>('RISING_ACTION')
  const [parentCommitId, setParentCommitId] = useState<string | null>(null)
  const [customAction, setCustomAction]   = useState('')
  const [generating, setGenerating]       = useState(false)

  // ── Active location (optimistic) ──────────────────────────────────────────
  const [activeLocation, setActiveLocation] = useState<string | null>(null)
  const [showLocSelector, setShowLocSelector] = useState(false)
  const locBtnRef = useRef<HTMLDivElement>(null)

  // Sync active_location from world state when it changes
  useEffect(() => {
    if (worldState?.metadata?.active_location) {
      setActiveLocation(worldState.metadata.active_location)
    }
  }, [worldState])

  // ── Import jobs ───────────────────────────────────────────────────────────
  const [jobId, setJobId]     = useState<string | null>(null)
  const [toasts, setToasts]   = useState<Toast[]>([])
  const pollRef               = useRef<ReturnType<typeof setInterval> | null>(null)

  // ── Typewriter for latest scene ───────────────────────────────────────────
  const latestSceneText = scenes[scenes.length - 1]?.scene_text ?? ''
  const { displayed: streamedText, done: streamDone } = useTypewriter(
    generating ? '' : latestSceneText,
  )

  // ── Auto-scroll to bottom of story feed ──────────────────────────────────
  const feedRef = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTo({ top: feedRef.current.scrollHeight, behavior: 'smooth' })
    }
  }, [scenes.length, streamedText])

  // ─── Toast helpers ────────────────────────────────────────────────────────

  const addToast = useCallback((message: string, kind: Toast['kind'] = 'info') => {
    const id = Math.random().toString(36).slice(2)
    setToasts(t => [...t, { id, message, kind }])
    setTimeout(() => setToasts(t => t.filter(x => x.id !== id)), 5000)
  }, [])

  const dismissToast = useCallback((id: string) => setToasts(t => t.filter(x => x.id !== id)), [])

  // ─── Import job polling ───────────────────────────────────────────────────

  useEffect(() => {
    if (!jobId) return
    pollRef.current = setInterval(async () => {
      try {
        const res = await fetch(`${API}/import/status/${jobId}`)
        if (!res.ok) return
        const data = await res.json()
        if (data.status === 'complete') {
          addToast(`Import done — ${data.result?.chunks ?? 1} chunk(s) indexed.`, 'success')
          setJobId(null)
          refreshWorld()
        } else if (data.status === 'error') {
          addToast(`Import error: ${data.error}`, 'error')
          setJobId(null)
        }
      } catch { /* keep polling */ }
    }, POLL_MS)
    return () => { if (pollRef.current) clearInterval(pollRef.current) }
  }, [jobId, addToast, refreshWorld])

  // ─── Set active location ──────────────────────────────────────────────────

  const selectLocation = useCallback(async (locId: string) => {
    setActiveLocation(locId)
    setShowLocSelector(false)
    try {
      await fetch(`${API}/api/active-location`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ location_id: locId }),
      })
      refreshWorld()
    } catch { /* non-fatal */ }
  }, [refreshWorld])

  // Close location selector on outside click
  useEffect(() => {
    const handler = (e: MouseEvent) => {
      if (locBtnRef.current && !locBtnRef.current.contains(e.target as Node)) {
        setShowLocSelector(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [])

  // ─── Generate scene ───────────────────────────────────────────────────────

  const generateScene = useCallback(async (playerAction: string, choiceTaken?: string) => {
    if (!playerAction.trim() || generating) return
    setGenerating(true)

    // Collect last 5 scenes as history for compression
    const history = scenes.slice(-5).map(s => s.scene_text)

    try {
      const res = await fetch(`${API}/actor/scene`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player_action: playerAction.trim(),
          freytag_stage: stage,
          parent_commit_id: parentCommitId,
          scene_history: history,
          transition_operations: activeLocation
            ? [{
                op: 'add',
                path: '/metadata/active_location',
                value: activeLocation,
              }]
            : [],
        }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data = await res.json()

      // Attach the choice that led here to the previous scene record
      if (choiceTaken) {
        setScenes(prev => {
          const updated = [...prev]
          if (updated.length > 0) updated[updated.length - 1] = { ...updated[updated.length - 1], choice_taken: `[${choiceTaken}]` }
          return updated
        })
      }

      const newEntry: SceneEntry = {
        scene_text: data.scene_text,
        choices: data.choices ?? { A: '', B: '', C: '' },
        commit_id: data.commit_id,
        active_location: data.active_location,
        axiom_violations: data.axiom_violations ?? [],
        branch_label: null,
        timestamp: Date.now(),
      }
      setScenes(prev => [...prev, newEntry])
      setParentCommitId(data.commit_id)
      if (data.active_location) setActiveLocation(data.active_location)

      // Advance branch HEAD
      await fetch(`${API}/actor/choose`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ choice: choiceTaken ?? 'A', commit_id: data.commit_id }),
      }).catch(() => { /* non-fatal */ })

      // Revalidate world state and branch data via SWR
      mutate(`${API}/api/world-state`)
      mutate(`${API}/chronos/branches`)

    } catch (e) {
      addToast(`Scene error: ${(e as Error).message}`, 'error')
    } finally {
      setGenerating(false)
      setCustomAction('')
    }
  }, [generating, stage, parentCommitId, scenes, activeLocation, addToast])

  // ─── Choice / submit handlers ─────────────────────────────────────────────

  const handleChoice = useCallback((letter: 'A' | 'B' | 'C') => {
    const latest = scenes[scenes.length - 1]
    if (!latest) return
    const choiceText = latest.choices[letter]
    if (!choiceText) return
    generateScene(choiceText, letter)
  }, [scenes, generateScene])

  const handleCustomSubmit = useCallback((e: React.FormEvent) => {
    e.preventDefault()
    if (!customAction.trim()) return
    generateScene(customAction)
  }, [customAction, generateScene])

  // ─── Derived values ───────────────────────────────────────────────────────

  const latestChoices: Choices = scenes[scenes.length - 1]?.choices ?? { A: '', B: '', C: '' }
  const hasChoices = Object.values(latestChoices).some(Boolean)
  const locNodes: WorldNode[] = worldState?.nodes ?? []
  const activeLocLabel = locNodes.find(n => n.id === activeLocation)?.label ?? activeLocation?.replace('loc_', '') ?? 'Unknown location'
  const commitCount = scenes.length

  // ─── Render ───────────────────────────────────────────────────────────────

  return (
    <div className="h-screen flex flex-col bg-slate-950 text-white overflow-hidden">

      {/* ── Top bar ────────────────────────────────────────────────────────── */}
      <div className="flex items-center gap-3 px-5 py-2.5 bg-slate-900 border-b border-slate-700 shrink-0">

        {/* Active location chip */}
        <div ref={locBtnRef} className="relative">
          <button
            onClick={() => setShowLocSelector(o => !o)}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-full bg-green-900/40 border border-green-700/50 text-green-400 text-xs font-medium hover:bg-green-900/70 transition-colors"
          >
            <span>📍</span>
            <span className="max-w-[120px] truncate">{activeLocLabel}</span>
            <span className="opacity-50">▾</span>
          </button>
          {showLocSelector && (
            <LocationSelector
              nodes={locNodes}
              current={activeLocation}
              onSelect={selectLocation}
              onClose={() => setShowLocSelector(false)}
            />
          )}
        </div>

        {/* Branch + commit count */}
        <div className="flex items-center gap-2 text-xs text-slate-500">
          <span className="font-mono text-teal-500">⑂ main</span>
          <span>·</span>
          <span>{commitCount} commit{commitCount !== 1 ? 's' : ''}</span>
        </div>

        <div className="flex-1" />

        <Link href="/map"       className="px-3 py-1.5 rounded text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors">🗺 Map</Link>
        <Link href="/timeline"  className="px-3 py-1.5 rounded text-sm text-slate-400 hover:text-white hover:bg-slate-700 transition-colors">⑂ Timeline</Link>
      </div>

      {/* ── Main layout ────────────────────────────────────────────────────── */}
      <div className="flex-1 grid grid-cols-[1fr_256px] overflow-hidden">

        {/* ── LEFT: Story feed + bottom bar ─────────────────────────────── */}
        <div className="flex flex-col overflow-hidden border-r border-slate-800">

          {/* Story feed */}
          <div ref={feedRef} className="flex-1 overflow-y-auto px-5 py-4 space-y-4">
            {scenes.length === 0 && !generating && (
              <div className="flex flex-col items-center justify-center h-full gap-4 text-slate-600">
                <p className="text-5xl">⚔</p>
                <p className="text-lg font-medium">Your story awaits</p>
                <p className="text-sm">Type an action below to begin.</p>
              </div>
            )}

            {scenes.map((entry, idx) => {
              const isLatest = idx === scenes.length - 1
              return (
                <SceneCard
                  key={entry.commit_id || idx}
                  entry={entry}
                  isLatest={isLatest}
                  displayedText={isLatest ? (generating ? '' : streamedText) : entry.scene_text}
                  typingDone={isLatest ? (!generating && streamDone) : true}
                />
              )
            })}

            {generating && (
              <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-slate-800/50 border border-slate-700">
                <div className="flex gap-1">
                  {[0, 1, 2].map(i => (
                    <span key={i} className="w-2 h-2 rounded-full bg-blue-400 animate-bounce" style={{ animationDelay: `${i * 150}ms` }} />
                  ))}
                </div>
                <span className="text-sm text-slate-400">Generating scene…</span>
              </div>
            )}
          </div>

          {/* ── Bottom bar: choices + custom input ───────────────────────── */}
          <div className="shrink-0 border-t border-slate-700 bg-slate-900/80 px-4 py-3 space-y-2">
            {/* Choice buttons */}
            {hasChoices && !generating && (
              <div className="grid grid-cols-3 gap-2">
                {(['A', 'B', 'C'] as const).map(letter => (
                  <button
                    key={letter}
                    onClick={() => handleChoice(letter)}
                    disabled={!latestChoices[letter] || generating}
                    className="flex flex-col items-start gap-0.5 px-3 py-2 rounded-lg bg-slate-800 hover:bg-slate-700 border border-slate-600 hover:border-slate-500 text-left transition-colors disabled:opacity-30 disabled:cursor-not-allowed"
                  >
                    <span className="text-[10px] font-bold text-slate-500">[{letter}]</span>
                    <span className="text-xs text-slate-300 leading-tight line-clamp-2">
                      {latestChoices[letter] || '—'}
                    </span>
                  </button>
                ))}
              </div>
            )}

            {/* Custom action input */}
            <form onSubmit={handleCustomSubmit} className="flex gap-2">
              <input
                value={customAction}
                onChange={e => setCustomAction(e.target.value)}
                placeholder={generating ? 'Generating…' : 'Or type a custom action…'}
                disabled={generating}
                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-sm text-white placeholder-slate-600 focus:border-blue-500 outline-none disabled:opacity-50"
              />
              <button
                type="submit"
                disabled={generating || !customAction.trim()}
                className="px-4 py-2 rounded-lg bg-blue-700 hover:bg-blue-600 disabled:opacity-40 text-white text-sm font-medium"
              >
                →
              </button>
            </form>
          </div>
        </div>

        {/* ── RIGHT: Freytag + import panel ─────────────────────────────── */}
        <div className="flex flex-col overflow-hidden bg-slate-900/50">
          <div className="flex-1 overflow-y-auto">
            <FreytagPanel active={stage} onChange={setStage} />
          </div>
          <ImportPanel addToast={addToast} onJobStarted={setJobId} />
        </div>

      </div>

      <ToastBar toasts={toasts} dismiss={dismissToast} />
    </div>
  )
}
