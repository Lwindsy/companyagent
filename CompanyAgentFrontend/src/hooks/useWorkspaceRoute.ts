import { useEffect, useState } from 'react'
import type { View } from '../types'

function readRoute(): { view: View; guide: string } {
  const query = new URLSearchParams(window.location.search)
  const view = query.get('view')
  return {
    view: view === 'admin' || view === 'guides' ? view : 'support',
    guide: query.get('guide') === 'technical' ? 'technical' : 'workflow',
  }
}

export function useWorkspaceRoute() {
  const [route, setRoute] = useState(readRoute)
  useEffect(() => {
    const onPopState = () => setRoute(readRoute())
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])
  function navigate(view: View, guide = route.guide) {
    const url = new URL(window.location.href)
    url.searchParams.set('view', view)
    if (view === 'guides') url.searchParams.set('guide', guide)
    else url.searchParams.delete('guide')
    url.hash = ''
    if (url.href !== window.location.href) window.history.pushState(null, '', url)
    setRoute({ view, guide })
  }
  return { ...route, navigate }
}
