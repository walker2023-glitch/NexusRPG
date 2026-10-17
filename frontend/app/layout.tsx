/**
 * Root layout — dark mode shell with persistent nav bar.
 * The nav bar fetches GET /api/health every 30 s and renders a status dot
 * for Ollama, LanceDB, world state, and Chronos commit count.
 */
import type { Metadata } from 'next'
import './globals.css'
import NavBar from './_components/NavBar'

export const metadata: Metadata = {
  title: 'Nexus RPG Engine',
  description: 'Local-first, CPU-only AI RPG — Architect + Actor modes',
}

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className="dark">
      <body className="bg-slate-950 text-white antialiased min-h-screen flex flex-col">
        <NavBar />
        <main className="flex-1 flex flex-col overflow-hidden">
          {children}
        </main>
      </body>
    </html>
  )
}
