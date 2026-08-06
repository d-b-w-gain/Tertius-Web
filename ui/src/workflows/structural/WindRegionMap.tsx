import { useEffect, useRef, useState } from 'react'
import L from 'leaflet'
import markerIconUrl from 'leaflet/dist/images/marker-icon.png'
import markerIconRetinaUrl from 'leaflet/dist/images/marker-icon-2x.png'
import markerShadowUrl from 'leaflet/dist/images/marker-shadow.png'
import 'leaflet/dist/leaflet.css'

import { apiFetch } from '../../api/client'
import type { SiteBaseMapMode } from '../site/SiteExplorer'


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

let geojsonCache: any = null

function isFeatureCollection(value: any) {
  return value?.type === 'FeatureCollection' && Array.isArray(value.features)
}

type Props = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  latitude: number | null
  longitude: number | null
  footprintLengthM?: number
  footprintWidthM?: number
  frontBearingDegrees?: number
  cardinalMultipliers?: Record<string, number> | null
  overlayMode?: 'wind' | 'terrain' | 'none'
  terrainEvidenceId?: string | null
  baseMapMode?: SiteBaseMapMode
  className?: string
  onPick: (latitude: number, longitude: number) => void
}

const CARDINAL_BEARINGS = [
  ['N', 0], ['NE', 45], ['E', 90], ['SE', 135],
  ['S', 180], ['SW', 225], ['W', 270], ['NW', 315],
] as const

function destination(
  latitude: number,
  longitude: number,
  bearingDegrees: number,
  distanceM: number,
): [number, number] {
  const radians = bearingDegrees * Math.PI / 180
  return [
    latitude + Math.cos(radians) * distanceM / 111_320,
    longitude + Math.sin(radians) * distanceM
      / Math.max(1, 111_320 * Math.cos(latitude * Math.PI / 180)),
  ]
}

export function structureFootprintCoordinates(
  latitude: number,
  longitude: number,
  footprintLengthM: number,
  footprintWidthM: number,
  frontBearingDegrees: number,
) {
  const radians = frontBearingDegrees * Math.PI / 180
  const metresPerLatitudeDegree = 111_320
  const metresPerLongitudeDegree = Math.max(
    1,
    metresPerLatitudeDegree * Math.cos(latitude * Math.PI / 180),
  )
  const point = (forward: number, right: number): [number, number] => {
    const north = Math.cos(radians) * forward - Math.sin(radians) * right
    const east = Math.sin(radians) * forward + Math.cos(radians) * right
    return [
      latitude + north / metresPerLatitudeDegree,
      longitude + east / metresPerLongitudeDegree,
    ]
  }
  const halfLength = footprintLengthM / 2
  const halfWidth = footprintWidthM / 2
  return [
    point(halfWidth, -halfLength),
    point(halfWidth, halfLength),
    point(-halfWidth, halfLength),
    point(-halfWidth, -halfLength),
  ]
}

export function WindRegionMap({
  serverUrl,
  getAccessToken,
  latitude,
  longitude,
  footprintLengthM = 12,
  footprintWidthM = 6,
  frontBearingDegrees = 0,
  cardinalMultipliers = null,
  overlayMode = 'wind',
  terrainEvidenceId = null,
  baseMapMode = 'street',
  className = 'h-64',
  onPick,
}: Props) {
  const divRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<any>(null)
  const markerRef = useRef<any>(null)
  const overlayRef = useRef<any>(null)
  const placementLayerRef = useRef<any>(null)
  const cardinalLayerRef = useRef<any>(null)
  const terrainLayerRef = useRef<any>(null)
  const baseLayerRef = useRef<any>(null)
  const onPickRef = useRef(onPick)
  const [status, setStatus] = useState<'loading' | 'ready' | 'failed'>('loading')
  const [message, setMessage] = useState('')

  useEffect(() => {
    onPickRef.current = onPick
  }, [onPick])

  useEffect(() => {
    if (!divRef.current || mapRef.current) return
    const map = L.map(divRef.current, {
      center: [-25, 134],
      zoom: 4,
      scrollWheelZoom: true,
    })
    map.on('click', (event: L.LeafletMouseEvent) => {
      onPickRef.current(event.latlng.lat, event.latlng.lng)
    })
    mapRef.current = map
    setStatus('ready')
    return () => {
      mapRef.current?.remove()
      mapRef.current = null
      markerRef.current = null
      overlayRef.current = null
      placementLayerRef.current = null
      cardinalLayerRef.current = null
      terrainLayerRef.current = null
      baseLayerRef.current = null
    }
  }, [])

  useEffect(() => {
    const map = mapRef.current
    baseLayerRef.current?.remove()
    baseLayerRef.current = null
    if (!map || status !== 'ready' || baseMapMode === 'none') return
    const satellite = baseMapMode === 'satellite'
    const layer = L.tileLayer(
      satellite
        ? 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}'
        : 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
      {
        attribution: satellite
          ? 'Imagery &copy; Esri and contributors'
          : '&copy; OpenStreetMap contributors',
        maxZoom: satellite ? 19 : 18,
      },
    ).addTo(map)
    layer.bringToBack()
    baseLayerRef.current = layer
    return () => {
      baseLayerRef.current?.remove()
      baseLayerRef.current = null
    }
  }, [baseMapMode, status])

  useEffect(() => {
    if (status !== 'ready') return
    const map = mapRef.current
    if (!map) return

    if (overlayMode !== 'wind') {
      overlayRef.current?.remove()
      overlayRef.current = null
      return
    }

    const apply = (geojson: any) => {
      overlayRef.current?.remove()
      overlayRef.current = L.geoJSON(geojson, {
        attribution: 'Wind regions &copy; Geoscience Australia (CC-BY 4.0)',
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
  }, [getAccessToken, overlayMode, serverUrl, status])

  useEffect(() => {
    const map = mapRef.current
    terrainLayerRef.current?.remove()
    terrainLayerRef.current = null
    if (!map || overlayMode !== 'terrain' || !terrainEvidenceId) return
    const terrainLayer = L.gridLayer({
      maxZoom: 22,
      opacity: 0.72,
      attribution: 'Terrain © Geoscience Australia',
    }) as any
    terrainLayer.createTile = (coords: { z: number, x: number, y: number }, done: (error: Error | null, tile: HTMLImageElement) => void) => {
      const tile = document.createElement('img')
      tile.alt = ''
      const url = `${serverUrl}/gis/evidence/${terrainEvidenceId}/relief/${coords.z}/${coords.x}/${coords.y}.png`
      void apiFetch(url, getAccessToken)
        .then((response) => {
          if (!response.ok) throw new Error(`Terrain tile returned ${response.status}`)
          return response.blob()
        })
        .then((blob) => {
          const objectUrl = URL.createObjectURL(blob)
          tile.onload = () => {
            URL.revokeObjectURL(objectUrl)
            done(null, tile)
          }
          tile.onerror = () => {
            URL.revokeObjectURL(objectUrl)
            done(new Error('Terrain tile could not be rendered'), tile)
          }
          tile.src = objectUrl
        })
        .catch((error) => done(error instanceof Error ? error : new Error('Terrain tile failed'), tile))
      return tile
    }
    terrainLayerRef.current = terrainLayer.addTo(map)
    return () => {
      terrainLayerRef.current?.remove()
      terrainLayerRef.current = null
    }
  }, [getAccessToken, overlayMode, serverUrl, status, terrainEvidenceId])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (latitude == null || longitude == null) {
      markerRef.current?.remove()
      markerRef.current = null
      return
    }
    if (markerRef.current) {
      markerRef.current.setLatLng([latitude, longitude])
    } else {
      const icon = L.icon({
        iconUrl: markerIconUrl,
        iconRetinaUrl: markerIconRetinaUrl,
        shadowUrl: markerShadowUrl,
        iconSize: [25, 41],
        iconAnchor: [12, 41],
        popupAnchor: [1, -34],
        shadowSize: [41, 41],
      })
      markerRef.current = L.marker([latitude, longitude], { icon }).addTo(map)
    }
    // A shed-scale footprint is only inspectable at parcel-level zoom.
    map.setView([latitude, longitude], Math.max(map.getZoom(), 18), {
      animate: true,
    })
  }, [latitude, longitude, status])

  useEffect(() => {
    const map = mapRef.current
    placementLayerRef.current?.remove()
    placementLayerRef.current = null
    if (!map || latitude == null || longitude == null) return
    if (footprintLengthM <= 0 || footprintWidthM <= 0) return

    const corners = structureFootprintCoordinates(
      latitude,
      longitude,
      footprintLengthM,
      footprintWidthM,
      frontBearingDegrees,
    )
    const [frontLeft, frontRight] = corners
    if (!frontLeft || !frontRight) return
    const frontMidpoint: [number, number] = [
      (frontLeft[0] + frontRight[0]) / 2,
      (frontLeft[1] + frontRight[1]) / 2,
    ]
    const group = L.layerGroup()
    L.polygon(corners, {
      color: '#5eead4',
      fillColor: '#0f766e',
      fillOpacity: 0.55,
      weight: 2,
    }).bindTooltip(
      `Structure footprint · front ${Math.round(frontBearingDegrees)}° true`,
      { sticky: true },
    ).addTo(group)
    L.polyline([[latitude, longitude], frontMidpoint], {
      color: '#fbbf24',
      weight: 4,
    }).addTo(group)
    group.addTo(map)
    placementLayerRef.current = group
  }, [
    footprintLengthM,
    footprintWidthM,
    frontBearingDegrees,
    latitude,
    longitude,
    status,
  ])

  useEffect(() => {
    const map = mapRef.current
    cardinalLayerRef.current?.remove()
    cardinalLayerRef.current = null
    if (!map || latitude == null || longitude == null) return
    const radius = Math.max(45, footprintLengthM * 4)
    const group = L.layerGroup()
    CARDINAL_BEARINGS.forEach(([direction, bearing]) => {
      const multiplier = cardinalMultipliers?.[direction.toLowerCase()] ?? 1
      const points: [number, number][] = [[latitude, longitude]]
      for (let offset = -22.5; offset <= 22.5; offset += 5.625) {
        points.push(destination(latitude, longitude, bearing + offset, radius))
      }
      L.polygon(points, {
        color: multiplier >= 1 ? '#fb7185' : '#38bdf8',
        fillColor: multiplier >= 1 ? '#be123c' : '#0369a1',
        fillOpacity: 0.12,
        opacity: 0.45,
        weight: 1,
      }).bindTooltip(`${direction} · Md ${multiplier.toFixed(2)}`).addTo(group)
    })
    group.addTo(map)
    cardinalLayerRef.current = group
  }, [cardinalMultipliers, footprintLengthM, latitude, longitude, status])

  return (
    <div className={`relative w-full overflow-hidden rounded border border-slate-700 bg-slate-900 ${className}`}>
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
