'use client'

import Link from 'next/link'
import { usePathname } from 'next/navigation'
import { useEffect, useState } from 'react'

const API = (process.env.NEXT_PUBLIC_API_URL ?? 'http://localhost:8000').replace(/\/$/, '')
const HEALTH_INTERVAL_MS = 30_000

interface HealthStatus {
  ollama: boolean
  lancedb: boolean
  world_state_loaded: boolean
  chronos_commits: number
}

const NAV_LINKS = [
  { href: '/map',      label: '🗺  Map' },
  { href: '/play',     label: '⚔  Play' },
  { href: '/timeline', label: '⑂  Timeline' },
] as const

// Dot: green = ok, red = error, gray = unknown/loading
function StatusDot({ ok }: { ok: boolean | null }) {
  if (ok === null) return <span className="w-2 h-2 rounded-full bg-slate-600 inline-block" />
  return (
    <span
      className={`w-2 h-2 rounded-full inline-block ${ok ? 'bg-green-400' : 'bg-red-500'}`}
      title={ok ? 'OK' : 'Offline'}
    />
  )
}

export default function NavBar() {
  const pathname = usePathname()
  const [health, setHealth]     = useState<HealthStatus | null>(null)
  const [fetchErr, setFetchErr] = useState(false)

  const fetchHealth = async () => {
    try {
      const res = await fetch(`${API}/api/health`, { cache: 'no-store' })
      if (!res.ok) throw new Error()
      setHealth(await res.json())
      setFetchErr(false)
    } catch {
      setFetchErr(true)
    }
  }

  useEffect(() => {
    fetchHealth()
    const id = setInterval(fetchHealth, HEALTH_INTERVAL_MS)
    return () => clearInterval(id)
  }, [])

  const allOk = health
    ? health.ollama && health.lancedb && health.world_state_loaded
    : null

  return (
    <nav className="flex items-center gap-1 px-4 py-2 bg-slate-900 border-b border-slate-800 shrink-0 select-none">
      {/* Brand */}
      <Link href="/" className="mr-3 flex items-center gap-2 font-bold text-sm tracking-tight text-white">
        <span className="text-base">⚙</span>
        <span>Nexus RPG</span>
      </Link>

      {/* Page links */}
      <div className="flex items-center gap-0.5">
        {NAV_LINKS.map(({ href, label }) => {
          const active = pathname === href || pathname.startsWith(href + '/')
          return (
            <Link
              key={href}
              href={href}
              className={`px-3 py-1.5 rounded-md text-sm transition-colors
                ${active
                  ? 'bg-slate-700 text-white font-medium'
                  : 'text-slate-400 hover:text-white hover:bg-slate-800'}`}
            >
              {label}
            </Link>
          )
        })}
      </div>

      <div className="flex-1" />

      {/* Health indicators */}
      <div className="flex items-center gap-3 text-xs text-slate-500">
        {/* Overall status pill */}
        <button
          onClick={fetchHealth}
          title="Click to refresh health"
          className={`flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-xs transition-colors
            ${fetchErr
              ? 'border-red-800 bg-red-900/30 text-red-400'
              : allOk === null
                ? 'border-slate-700 bg-slate-800/50 text-slate-500'
                : allOk
                  ? 'border-green-800 bg-green-900/30 text-green-400'
                  : 'border-amber-800 bg-amber-900/30 text-amber-400'}`}
        >
          <StatusDot ok={fetchErr ? false : allOk} />
          <span>
            {fetchErr ? 'API offline' : allOk === null ? 'Checking…' : allOk ? 'Ready' : 'Degraded'}
          </span>
        </button>

        {/* Per-subsystem dots — visible when health data is available */}
        {health && (
          <div className="flex items-center gap-2">
            <span className="flex items-center gap-1">
              <StatusDot ok={health.ollama} />
              <span>Ollama</span>
            </span>
            <span className="flex items-center gap-1">
              <StatusDot ok={health.lancedb} />
              <span>LanceDB</span>
            </span>
            <span className="flex items-center gap-1">
              <StatusDot ok={health.world_state_loaded} />
              <span>World</span>
            </span>
            <span className="text-slate-600">
              {health.chronos_commits}c
            </span>
          </div>
        )}
      </div>
    </nav>
  )
}
