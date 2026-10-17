import { redirect } from 'next/navigation'

/**
 * Root route — redirect immediately to the Map view.
 * All real UI lives under /map, /play, and /timeline.
 */
export default function Home() {
  redirect('/map')
}
