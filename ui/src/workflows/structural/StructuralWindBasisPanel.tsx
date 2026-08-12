import type { StructuralWindActionBasis } from './contracts'

type StructuralWindBasisPanelProps = {
  bases: StructuralWindActionBasis[]
}

const directionOrder = ['+Y', '+X', '-X', '-Y'] as const

function DirectionCard({ basis }: { basis: StructuralWindActionBasis }) {
  const contributors = basis.contributing_cardinal_directions?.join(' / ') || '—'
  return (
    <div className="rounded border border-cyan-900/70 bg-slate-950/80 px-3 py-2">
      <div className="flex items-center justify-between gap-3">
        <span className="text-xs font-bold text-cyan-200">
          {basis.structural_action_direction} · {basis.building_face} face
        </span>
        <span className="font-mono text-sm font-semibold text-slate-100">
          {basis.q_z_kPa.toFixed(3)} kPa
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 text-[10px] text-slate-400">
        <span>{contributors} → {basis.governing_cardinal_direction || '—'}</span>
        {basis.face_bearing_degrees != null && (
          <span>{basis.face_bearing_degrees.toFixed(0)}° true</span>
        )}
        <span>{basis.site_wind_speed_m_s.toFixed(2)} m/s</span>
      </div>
    </div>
  )
}

export function StructuralWindBasisPanel({ bases }: StructuralWindBasisPanelProps) {
  if (bases.length === 0) return null

  const directional = new Map(
    bases
      .filter((basis) => basis.structural_action_direction)
      .map((basis) => [basis.structural_action_direction, basis]),
  )

  if (directional.size !== 4) {
    const basis = bases[0]!
    return (
      <section className="shrink-0 border-b border-slate-800 bg-slate-900/50 px-5 py-2">
        <div className="flex items-center justify-between gap-4 text-xs">
          <span className="font-semibold text-slate-300">Wind action basis</span>
          <span className="text-amber-300">Single conservative site envelope</span>
          <span className="font-mono text-slate-100">
            {basis.q_z_kPa.toFixed(3)} kPa · {basis.site_wind_speed_m_s.toFixed(2)} m/s
          </span>
        </div>
      </section>
    )
  }

  return (
    <details className="group shrink-0 border-b border-cyan-950 bg-slate-900/50" open>
      <summary className="flex cursor-pointer list-none items-center gap-3 px-5 py-2 text-xs">
        <span className="font-semibold text-slate-200">Directional wind basis</span>
        <span className="text-slate-500">cardinal exposure → building face → structural axis</span>
        <span className="ml-auto text-[10px] font-semibold uppercase tracking-[0.14em] text-cyan-400 group-open:hidden">
          Show
        </span>
        <span className="ml-auto hidden text-[10px] font-semibold uppercase tracking-[0.14em] text-cyan-400 group-open:inline">
          Hide
        </span>
      </summary>
      <div className="grid grid-cols-1 gap-2 px-5 pb-3 md:grid-cols-2 xl:grid-cols-4">
        {directionOrder.map((direction) => (
          <DirectionCard key={direction} basis={directional.get(direction)!} />
        ))}
      </div>
    </details>
  )
}
