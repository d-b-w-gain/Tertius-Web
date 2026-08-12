import type { SiteCalculation, SiteDefinition } from './contracts'


type DirectionKey = keyof NonNullable<SiteDefinition['wind']['cardinal_direction_multipliers']>

const DIRECTIONS: Array<{ key: DirectionKey, label: string, bearing: number }> = [
  { key: 'n', label: 'N', bearing: 0 },
  { key: 'ne', label: 'NE', bearing: 45 },
  { key: 'e', label: 'E', bearing: 90 },
  { key: 'se', label: 'SE', bearing: 135 },
  { key: 's', label: 'S', bearing: 180 },
  { key: 'sw', label: 'SW', bearing: 225 },
  { key: 'w', label: 'W', bearing: 270 },
  { key: 'nw', label: 'NW', bearing: 315 },
]

const inputClass = (
  'w-full rounded border border-slate-700 bg-slate-950 px-3 py-2 '
  + 'text-sm text-slate-100 outline-none focus:border-cyan-500'
)

type Props = {
  structure: SiteDefinition['structure']
  multipliers: SiteDefinition['wind']['cardinal_direction_multipliers']
  fallbackMultiplier: number
  calculation: SiteCalculation | null
  onStructureChange: (structure: SiteDefinition['structure']) => void
  onMultipliersChange: (
    multipliers: SiteDefinition['wind']['cardinal_direction_multipliers'],
  ) => void
}

function polarPoint(bearing: number, radius: number) {
  const radians = bearing * Math.PI / 180
  return {
    x: 120 + Math.sin(radians) * radius,
    y: 120 - Math.cos(radians) * radius,
  }
}

function displayBearing(value: number) {
  return `${Math.round(value)}°`
}

export function StructureWindRose({
  structure,
  multipliers,
  fallbackMultiplier,
  calculation,
  onStructureChange,
  onMultipliersChange,
}: Props) {
  const displayedMultipliers = multipliers ?? Object.fromEntries(
    DIRECTIONS.map(({ key }) => [key, fallbackMultiplier]),
  ) as NonNullable<SiteDefinition['wind']['cardinal_direction_multipliers']>
  const maxMultiplier = Math.max(...Object.values(displayedMultipliers), 0.01)
  const maxSiteSpeed = Math.max(
    ...(calculation?.cardinal_wind_speeds.map((sector) => sector.site_wind_speed_m_s) ?? [0.01]),
    0.01,
  )

  const updateStructure = <K extends keyof SiteDefinition['structure']>(
    key: K,
    value: SiteDefinition['structure'][K],
  ) => onStructureChange({ ...structure, [key]: value })

  const updateMultiplier = (key: DirectionKey, value: number) => {
    onMultipliersChange({ ...displayedMultipliers, [key]: value })
  }

  return (
    <section id="structure-orientation" className="rounded border border-cyan-500/40 bg-cyan-950/10 p-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="font-semibold text-slate-100">Structure orientation &amp; directional wind</h2>
          <p className="mt-1 max-w-3xl text-xs leading-5 text-slate-400">
            Place the footprint relative to true north. Cardinal wind sectors are calculated first,
            then rotated into the building&apos;s front, right, back and left design directions.
          </p>
        </div>
        <span className={`rounded px-2 py-1 text-[10px] font-bold uppercase ${
          structure.orientation_status === 'verified'
            ? 'bg-emerald-500/20 text-emerald-300'
            : 'bg-amber-500/20 text-amber-300'
        }`}>
          {structure.orientation_status === 'verified' ? 'orientation checked' : 'orientation suggested'}
        </span>
      </div>

      <div className="mt-4 grid gap-5 lg:grid-cols-[17rem_minmax(0,1fr)]">
        <div>
          <div className="mx-auto aspect-square max-w-64 rounded-full border border-slate-700 bg-slate-950/80 p-2">
            <svg viewBox="0 0 240 240" role="img" aria-label="Directional wind rose and rotated structure footprint">
              <circle cx="120" cy="120" r="91" fill="none" stroke="#334155" />
              <circle cx="120" cy="120" r="61" fill="none" stroke="#1e293b" />
              {DIRECTIONS.map(({ key, label, bearing }) => {
                const labelPoint = polarPoint(bearing, 105)
                const sector = calculation?.cardinal_wind_speeds.find((item) => item.direction === label)
                const relativeValue = sector
                  ? sector.site_wind_speed_m_s / maxSiteSpeed
                  : displayedMultipliers[key] / maxMultiplier
                const rayPoint = polarPoint(bearing, 43 + 43 * relativeValue)
                return (
                  <g key={key}>
                    <line x1="120" y1="120" x2={rayPoint.x} y2={rayPoint.y}
                      stroke="#22d3ee" strokeWidth="5" strokeLinecap="round" opacity="0.62" />
                    <text x={labelPoint.x} y={labelPoint.y + 3} textAnchor="middle"
                      fill="#cbd5e1" fontSize="10" fontWeight="700">
                      {label}
                    </text>
                    {sector && (
                      <title>{`${label}: Md ${sector.direction_multiplier.toFixed(3)} × Mz,cat ${(sector.terrain_height_multiplier ?? calculation?.terrain_height_multiplier ?? 1).toFixed(3)} × Ms ${(sector.shielding_multiplier ?? 1).toFixed(3)} × Mt ${(sector.topographic_multiplier ?? 1).toFixed(3)} = ${sector.site_wind_speed_m_s.toFixed(2)} m/s`}</title>
                    )}
                  </g>
                )
              })}
              <g transform={`rotate(${structure.front_bearing_degrees} 120 120)`}>
                <rect x="78" y="96" width="84" height="48" rx="3"
                  fill="#0f766e" fillOpacity="0.85" stroke="#5eead4" strokeWidth="2" />
                <line x1="120" y1="96" x2="120" y2="63" stroke="#fbbf24" strokeWidth="3" />
                <path d="M 120 57 L 114 67 L 126 67 Z" fill="#fbbf24" />
                <text x="120" y="91" textAnchor="middle" fill="#fef3c7" fontSize="8" fontWeight="700">FRONT</text>
              </g>
              <circle cx="120" cy="120" r="3" fill="#f8fafc" />
            </svg>
          </div>
          <p className="mt-2 text-center font-mono text-xs text-cyan-200">
            Front bearing {displayBearing(structure.front_bearing_degrees)} true
          </p>
        </div>

        <div className="space-y-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Front bearing (° true)</span>
              <input aria-label="Front bearing degrees true" type="number" min="0" max="359.9" step="1"
                className={`${inputClass} mt-1`} value={structure.front_bearing_degrees}
                onChange={(event) => updateStructure('front_bearing_degrees', Number(event.target.value))} />
            </label>
            <label className="block">
              <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Front definition</span>
              <select className={`${inputClass} mt-1`} value={structure.front_definition}
                onChange={(event) => updateStructure(
                  'front_definition',
                  event.target.value as SiteDefinition['structure']['front_definition'],
                )}>
                <option value="long_wall_normal">Normal to long wall</option>
                <option value="gable_ridge_normal">Normal to roof ridge</option>
                <option value="manual">Manually nominated face</option>
              </select>
            </label>
            <label className="block">
              <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Footprint length (m)</span>
              <input aria-label="Footprint length metres" type="number" min="0.1" step="0.1"
                className={`${inputClass} mt-1`} value={structure.footprint_length_m}
                onChange={(event) => updateStructure('footprint_length_m', Number(event.target.value))} />
            </label>
            <label className="block">
              <span className="text-[10px] font-bold uppercase tracking-wide text-slate-400">Footprint depth (m)</span>
              <input aria-label="Footprint depth metres" type="number" min="0.1" step="0.1"
                className={`${inputClass} mt-1`} value={structure.footprint_width_m}
                onChange={(event) => updateStructure('footprint_width_m', Number(event.target.value))} />
            </label>
          </div>
          <input aria-label="Rotate structure" type="range" min="0" max="359" step="1"
            className="w-full accent-cyan-500" value={structure.front_bearing_degrees}
            onChange={(event) => updateStructure('front_bearing_degrees', Number(event.target.value))} />
          <label className={`flex items-start gap-2 rounded border p-3 text-xs ${
            structure.orientation_status === 'verified'
              ? 'border-emerald-500/40 bg-emerald-950/20'
              : 'border-amber-500/50 bg-amber-950/20'
          }`}>
            <input type="checkbox" className="mt-0.5"
              checked={structure.orientation_status === 'verified'}
              onChange={(event) => updateStructure(
                'orientation_status', event.target.checked ? 'verified' : 'suggested',
              )} />
            <span><b>Structure bearing checked against site north</b><br />
              <span className="text-slate-500">Confirm against the survey/site plan before design use.</span>
            </span>
          </label>
        </div>
      </div>

      <div className="mt-5 border-t border-slate-800 pt-4">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div>
            <h3 className="text-sm font-semibold text-slate-200">Eight cardinal Md values</h3>
            <p className="mt-1 text-xs text-slate-500">
              Enter values verified from the licensed project standard. Until enabled, the single
              conservative Md is used for every direction.
            </p>
          </div>
          {multipliers === null && (
            <button type="button" className="rounded border border-cyan-500/50 px-3 py-2 text-xs font-semibold text-cyan-200 hover:bg-cyan-950"
              onClick={() => onMultipliersChange(displayedMultipliers)}>
              Enable cardinal inputs
            </button>
          )}
        </div>
        <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 lg:grid-cols-8">
          {DIRECTIONS.map(({ key, label }) => (
            <label key={key} className="block">
              <span className="block text-center text-[10px] font-bold text-slate-400">{label}</span>
              <input aria-label={`${label} direction multiplier`} type="number" min="0.01" step="0.01"
                disabled={multipliers === null}
                className={`${inputClass} mt-1 px-2 text-center font-mono disabled:opacity-45`}
                value={displayedMultipliers[key]}
                onChange={(event) => updateMultiplier(key, Number(event.target.value))} />
            </label>
          ))}
        </div>
      </div>

      {calculation && (
        <div className="mt-5 border-t border-slate-800 pt-4">
          <h3 className="text-sm font-semibold text-slate-200">Complete directional site speeds</h3>
          <p className="mt-1 text-xs text-slate-500">
            Each sector composes its own Md × Mz,cat × Ms × Mt before any building-face maximum is taken.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4 xl:grid-cols-8">
            {calculation.cardinal_wind_speeds.map((sector) => (
              <div key={sector.direction} className="rounded border border-slate-800 bg-slate-950/70 p-2 text-center">
                <div className="text-[10px] font-bold text-slate-400">{sector.direction}</div>
                <div className="mt-1 font-mono text-sm text-cyan-200">{sector.site_wind_speed_m_s.toFixed(2)}</div>
                <div className="text-[9px] text-slate-600">m/s</div>
                <div className="mt-2 font-mono text-[9px] leading-4 text-slate-500">
                  {sector.direction_multiplier.toFixed(2)} × {(sector.terrain_height_multiplier ?? calculation.terrain_height_multiplier).toFixed(2)}<br />
                  × {(sector.shielding_multiplier ?? 1).toFixed(2)} × {(sector.topographic_multiplier ?? 1).toFixed(2)}
                </div>
              </div>
            ))}
          </div>

          <div className="mt-5 border-t border-slate-800 pt-4">
          <h3 className="text-sm font-semibold text-slate-200">Building design directions</h3>
          <p className="mt-1 text-xs text-slate-500">
            Each face takes the worst cardinal site speed within ±45° of its outward bearing.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-2 lg:grid-cols-4">
            {calculation.building_face_wind_speeds.map((face) => (
              <div key={face.face} className="rounded border border-slate-800 bg-slate-950/70 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="text-[10px] font-bold uppercase text-slate-400">{face.face}</span>
                  <span className="font-mono text-[10px] text-amber-200">{displayBearing(face.bearing_degrees)}</span>
                </div>
                <div className="mt-2 font-mono text-sm text-cyan-200">{face.site_wind_speed_m_s.toFixed(2)} m/s</div>
                <div className="mt-1 text-[10px] text-slate-500">
                  Governing {face.governing_cardinal_direction} · qz {face.q_z_kPa.toFixed(3)} kPa
                </div>
              </div>
            ))}
          </div>
          </div>
        </div>
      )}
    </section>
  )
}
