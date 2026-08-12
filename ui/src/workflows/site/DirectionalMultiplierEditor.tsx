import type { CardinalMultiplierValues } from './contracts'


const DIRECTIONS: Array<{ key: keyof CardinalMultiplierValues, label: string }> = [
  { key: 'n', label: 'N' },
  { key: 'ne', label: 'NE' },
  { key: 'e', label: 'E' },
  { key: 'se', label: 'SE' },
  { key: 's', label: 'S' },
  { key: 'sw', label: 'SW' },
  { key: 'w', label: 'W' },
  { key: 'nw', label: 'NW' },
]

type Props = {
  label: string
  symbol: string
  values: CardinalMultiplierValues | null
  fallback: number
  fallbackLabel: string
  onChange: (values: CardinalMultiplierValues | null) => void
}

export function DirectionalMultiplierEditor({
  label,
  symbol,
  values,
  fallback,
  fallbackLabel,
  onChange,
}: Props) {
  const displayed = values ?? Object.fromEntries(
    DIRECTIONS.map(({ key }) => [key, fallback]),
  ) as CardinalMultiplierValues

  return (
    <div className="rounded border border-slate-800 bg-slate-950/50 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div>
          <span className="text-xs font-semibold text-slate-200">{label}</span>
          <span className="ml-2 font-mono text-[10px] text-cyan-300">{symbol}</span>
          <p className="mt-1 text-[10px] text-slate-500">
            {values === null ? fallbackLabel : 'Eight independent reviewed inputs are active.'}
          </p>
        </div>
        <button type="button"
          className="rounded border border-slate-700 px-2 py-1 text-[10px] font-semibold text-slate-300 hover:border-cyan-500"
          onClick={() => onChange(values === null ? displayed : null)}>
          {values === null ? 'Enable 8 directions' : 'Use fallback'}
        </button>
      </div>
      <div className="mt-3 grid grid-cols-4 gap-2 lg:grid-cols-8">
        {DIRECTIONS.map(({ key, label: direction }) => (
          <label key={key} className="block">
            <span className="block text-center text-[10px] font-bold text-slate-500">{direction}</span>
            <input aria-label={`${direction} ${label}`} type="number" min="0.01" max="5" step="0.01"
              disabled={values === null}
              value={displayed[key]}
              onChange={(event) => onChange({
                ...displayed,
                [key]: Number(event.target.value),
              })}
              className="mt-1 w-full rounded border border-slate-700 bg-slate-950 px-1 py-2 text-center font-mono text-xs text-slate-100 outline-none focus:border-cyan-500 disabled:opacity-45" />
          </label>
        ))}
      </div>
    </div>
  )
}
