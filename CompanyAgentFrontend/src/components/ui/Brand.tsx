import { AudioLines } from 'lucide-react'

export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <span className="brand">
      <span className="brand-symbol">
        <AudioLines size={24} strokeWidth={1.8} />
      </span>
      {!compact && (
        <span>
          support<span className="brand-dot">.</span>
        </span>
      )}
    </span>
  )
}

export function SupportOrb({ small = false }: { small?: boolean }) {
  return (
    <div className={`support-orb ${small ? 'small' : ''}`} aria-hidden="true">
      <div className="orb-orbit orbit-one" />
      <div className="orb-orbit orbit-two" />
      <div className="orb-core">
        <AudioLines size={small ? 23 : 40} strokeWidth={1.5} />
      </div>
      <span className="orb-satellite satellite-one" />
      <span className="orb-satellite satellite-two" />
    </div>
  )
}
