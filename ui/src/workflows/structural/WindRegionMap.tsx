import { useEffect, useRef, useState } from 'react'

import { apiFetch } from '../../api/client'


const LEAFLET_CSS = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.css'
const LEAFLET_JS = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/leaflet.js'
const ICON_BASE = 'https://cdn.jsdelivr.net/npm/leaflet@1.9.4/dist/images/'

const REGION_FILL: Record<string, string> = {
  A0: '#22c55e',
  A1: '#22c55e',
  A2: '#22c55e',
  A3: '#22c55e',
  A4: '#22c55e',
  A5: '#22c55e',
  B1: '#f59e0b',
  B2: '#f59e0b',
  C: '#ef4444',
  D: '#b91c1c',
}

let leafletPromise: Promise<any> | null = null
let geojsonCache: any = null

function loadLeaflet(): Promise<any> {
  if (leafletPromise) return leafletPromise
  leafletPromise = new Promise((resolve, reject) => {
    const existing = (window as any).L
    if (existing) {
      resolve(existing)
      return
    }
    if (!document.querySelector(`link[href="${LEAFLET_CSS}"]`)) {
      const link = document.createElement('link')
      link.rel = 'stylesheet'
      link.href = LEAFLET_CSS
      document.head.appendChild(link)
    }
    const script = document.createElement('script')
    script.src = LEAFLET_JS
    script.async = true
    script.onload = () => resolve((window as any).L)
    script.onerror = () => reject(new Error('Leaflet CDN load failed'))
    document.head.appendChild(script)
  })
  return leafletPromise
}

function isFeatureCollection(value: any) {
  return value?.type === 'FeatureCollection' && Array.isArray(value.features)
}

type Props = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  latitude: number | null
  longitude: number | null
  onPick: (latitude: number, longitude: number) => void
}

export function WindRegionMap({
  serverUrl,
  getAccessToken,
  latitude,
  longitude,
  onPick,
}: Props) {
  const divRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const markerRef = useRef<any>(null)
  const overlayRef = useRef<any>(null)
  const leafletRef = useRef<any>(null)
  const onPickRef = useRef(onPick)
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [message, setMessage] = useState('')

  useEffect(() => {
    onPickRef.current = onPick
  }, [onPick])

  useEffect(() => {
    let cancelled = false
    void loadLeaflet()
      .then((L) => {
        if (cancelled || !divRef.current || mapRef.current) return
        leafletRef.current = L
        const map = L.map(divRef.current, {
          center: [-25, 134],
          zoom: 4,
          scrollWheelZoom: true,
        })
        L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
          attribution: (
            '&copy; OpenStreetMap contributors · '
            + 'Wind regions &copy; Geoscience Australia (CC-BY 4.0)'
          ),
          maxZoom: 18,
        }).addTo(map)
        map.on('click', (event: any) => {
          onPickRef.current(event.latlng.lat, event.latlng.lng)
        })
        mapRef.current = map
        setStatus('ready')
      })
      .catch((error) => {
        if (cancelled) return
        setStatus('failed')
        setMessage(error instanceof Error ? error.message : 'Map failed to load')
      })
    return () => {
      cancelled = true
      mapRef.current?.remove()
      mapRef.current = null
      markerRef.current = null
      overlayRef.current = null
    }
  }, [])

  useEffect(() => {
    if (status !== 'ready') return
    const L = leafletRef.current
    const map = mapRef.current
    if (!L || !map) return

    const apply = (geojson: any) => {
      overlayRef.current?.remove()
      overlayRef.current = L.geoJSON(geojson, {
        style: (feature: any) => {
          const colour = REGION_FILL[feature?.properties?.region] ?? '#94a3b8'
          return {
            fillColor: colour,
            fillOpacity: 0.22,
            color: colour,
            weight: 1,
            opacity: 0.75,
          }
        },
        onEachFeature: (feature: any, layer: any) => {
          const properties = feature?.properties ?? {}
          layer.bindTooltip(
            `<strong>${properties.region ?? '?'}</strong> — ${properties.area ?? ''}`,
            { sticky: true, direction: 'top' },
          )
        },
      }).addTo(map)
      setMessage('')
    }

    if (isFeatureCollection(geojsonCache)) {
      apply(geojsonCache)
      return
    }
    let cancelled = false
    void apiFetch(`${serverUrl}/wind/regions.geojson`, getAccessToken)
      .then(async (response) => {
        if (!response.ok) throw new Error(`Region overlay returned ${response.status}`)
        return response.json()
      })
      .then((payload) => {
        if (cancelled) return
        if (!isFeatureCollection(payload)) {
          throw new Error('Region overlay returned an unexpected response')
        }
        geojsonCache = payload
        apply(payload)
      })
      .catch((error) => {
        if (!cancelled) {
          setMessage(error instanceof Error ? error.message : 'Region overlay failed')
        }
      })
    return () => {
      cancelled = true
    }
  }, [getAccessToken, serverUrl, status])

  useEffect(() => {
    const L = leafletRef.current
    const map = mapRef.current
    if (!L || !map) return
    if (latitude == null || longitude == null) {
      markerRef.current?.remove()
      markerRef.current = null
      return
    }
    if (markerRef.current) {
      markerRef.current.setLatLng([latitude, longitude])
    } else {
      const icon = L.icon({
        iconUrl: `${ICON_BASE}marker-icon.png`,
        iconRetinaUrl: `${ICON_BASE}marker-icon-2x.png`,
        shadowUrl: `${ICON_BASE}marker-shadow.png`,
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41],
      })
      markerRef.current = L.marker([latitude, longitude], { icon }).addTo(map)
    }
    map.setView([latitude, longitude], Math.max(map.getZoom(), 11), {
      animate: true,
    })
  }, [latitude, longitude, status])

  return (
    <div className="relative h-64 w-full overflow-hidden rounded border border-slate-700 bg-slate-900">
      <div ref={divRef} className="h-full w-full" />
      {status === 'loading' && (
        <div className="absolute inset-0 flex items-center justify-center bg-slate-950/80 text-xs text-slate-400">
          Loading wind-region map…
        </div>
      )}
      {(status === 'failed' || message) && (
        <div className="absolute left-2 right-2 top-2 rounded border border-amber-500/40 bg-slate-950/90 p-2 text-[9px] text-amber-200">
          {message || 'Map unavailable. Coordinates can still be entered manually.'}
        </div>
      )}
      {status === 'ready' && (
        <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-slate-950/80 px-2 py-1 text-[9px] text-slate-300">
          Click to choose site coordinates
        </div>
      )}
    </div>
  )
}
