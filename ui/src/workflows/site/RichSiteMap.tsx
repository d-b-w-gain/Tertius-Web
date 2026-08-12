import { useEffect, useRef, useState } from 'react'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

import { apiFetch } from '../../api/client'
import { structureFootprintCoordinates } from '../structural/WindRegionMap'
import { loadCandidateDesignLayer } from './CandidateDesignLayer'
import type { CandidateRepresentation, CandidateUpAxis } from './CandidateDesignLayer'
import type { SiteBaseMapMode, SiteGroundMode } from './SiteExplorer'
import type {
  GisBuildingEvidence,
  GisDirectionalWindMultiplierEvidence,
  GisSiteBoundaryEvidence,
} from './contracts'


const DIRECTIONS = [
  ['N', 0], ['NE', 45], ['E', 90], ['SE', 135],
  ['S', 180], ['SW', 225], ['W', 270], ['NW', 315],
] as const

type Props = {
  serverUrl: string
  getAccessToken: () => Promise<string>
  latitude: number
  longitude: number
  footprintLengthM: number
  footprintWidthM: number
  frontBearingDegrees: number
  referenceHeightM: number
  cardinalMultipliers: Record<string, number> | null
  overlayMode: 'wind' | 'terrain' | 'none'
  baseMapMode: SiteBaseMapMode
  terrainMode: SiteGroundMode
  terrainEvidenceId: string | null
  terrainEvidenceBounds: [number, number, number, number] | null
  candidateModelUrl: string | null
  candidateRepresentation: CandidateRepresentation
  cameraMode: 'plan' | 'perspective'
  siteBoundary: GisSiteBoundaryEvidence | null
  buildingEvidence: GisBuildingEvidence | null
  directionalEvidence: GisDirectionalWindMultiplierEvidence | null
  onPick: (latitude: number, longitude: number) => void
}

function destination(
  latitude: number,
  longitude: number,
  bearingDegrees: number,
  distanceM: number,
) {
  const radians = bearingDegrees * Math.PI / 180
  return [
    longitude + Math.sin(radians) * distanceM
      / Math.max(1, 111_320 * Math.cos(latitude * Math.PI / 180)),
    latitude + Math.cos(radians) * distanceM / 111_320,
  ]
}

function siteFeatures(props: Props) {
  const {
    latitude, longitude, footprintLengthM, footprintWidthM,
    frontBearingDegrees, referenceHeightM, cardinalMultipliers,
  } = props
  const radius = Math.max(45, footprintLengthM * 4)
  const sectors = DIRECTIONS.map(([direction, bearing]) => {
    const coordinates = [[longitude, latitude]]
    for (let offset = -22.5; offset <= 22.5; offset += 5.625) {
      coordinates.push(destination(latitude, longitude, bearing + offset, radius))
    }
    coordinates.push([longitude, latitude])
    return {
      type: 'Feature',
      properties: {
        direction,
        multiplier: cardinalMultipliers?.[direction.toLowerCase()] ?? 1,
      },
      geometry: { type: 'Polygon', coordinates: [coordinates] },
    }
  })
  const corners = structureFootprintCoordinates(
    latitude,
    longitude,
    footprintLengthM,
    footprintWidthM,
    frontBearingDegrees,
  )
  const footprint = corners.map(([lat, lon]) => [lon, lat])
  const firstCorner = footprint[0]!
  const secondCorner = footprint[1]!
  footprint.push(firstCorner)
  const frontMidpoint = [
    (firstCorner[0]! + secondCorner[0]!) / 2,
    (firstCorner[1]! + secondCorner[1]!) / 2,
  ]
  return {
    sectors: { type: 'FeatureCollection', features: sectors },
    structure: {
      type: 'FeatureCollection',
      features: [{
        type: 'Feature',
        properties: { height: Math.max(2.4, referenceHeightM) },
        geometry: { type: 'Polygon', coordinates: [footprint] },
      }, {
        type: 'Feature',
        properties: { kind: 'front' },
        geometry: {
          type: 'LineString',
          coordinates: [[longitude, latitude], frontMidpoint],
        },
      }],
    },
  }
}

export function RichSiteMap(props: Props) {
  const { getAccessToken } = props
  const divRef = useRef<HTMLDivElement>(null)
  const mapRef = useRef<maplibregl.Map | null>(null)
  const propsRef = useRef(props)
  const initialPositionRef = useRef({ latitude: props.latitude, longitude: props.longitude })
  const initialBaseMapRef = useRef(props.baseMapMode)
  const initialCameraModeRef = useRef(props.cameraMode)
  const [message, setMessage] = useState('Loading 3D site view…')
  const [accessToken, setAccessToken] = useState<string | null>(null)
  const [candidateUpAxis, setCandidateUpAxis] = useState<CandidateUpAxis | null>(null)
  const [candidateSizeM, setCandidateSizeM] = useState<[number, number, number] | null>(null)
  const [viewZoom, setViewZoom] = useState(18)

  useEffect(() => {
    propsRef.current = props
  }, [props])

  useEffect(() => {
    let cancelled = false
    void getAccessToken()
      .then((token) => {
        if (!cancelled) setAccessToken(token)
      })
      .catch((error) => {
        if (!cancelled) setMessage(error instanceof Error ? error.message : '3D map authentication failed')
      })
    return () => {
      cancelled = true
    }
  }, [getAccessToken])

  useEffect(() => {
    if (!divRef.current || mapRef.current || accessToken === null) return
    const initialPosition = initialPositionRef.current
    const map = new maplibregl.Map({
      container: divRef.current,
      style: {
        version: 8,
        sources: {
          street: {
            type: 'raster',
            tiles: ['https://tile.openstreetmap.org/{z}/{x}/{y}.png'],
            tileSize: 256,
            maxzoom: 19,
            attribution: '© OpenStreetMap contributors',
          },
          satellite: {
            type: 'raster',
            tiles: [
              'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            ],
            tileSize: 256,
            maxzoom: 19,
            attribution: 'Imagery © Esri and contributors',
          },
          nsw: {
            type: 'raster',
            tiles: [
              'https://portal.spatial.nsw.gov.au/aid/tile/rest/services/NSWWebImagery/MapServer/tile/{z}/{y}/{x}',
            ],
            tileSize: 256,
            maxzoom: 23,
            attribution: 'Imagery © NSW Spatial Services',
          },
        },
        layers: [{
          id: 'street', type: 'raster', source: 'street',
          layout: { visibility: initialBaseMapRef.current === 'street' ? 'visible' : 'none' },
        }, {
          id: 'satellite', type: 'raster', source: 'satellite',
          layout: { visibility: initialBaseMapRef.current === 'satellite' ? 'visible' : 'none' },
        }, {
          id: 'nsw', type: 'raster', source: 'nsw',
          layout: { visibility: initialBaseMapRef.current === 'nsw' ? 'visible' : 'none' },
        }],
      },
      center: [initialPosition.longitude, initialPosition.latitude],
      zoom: initialCameraModeRef.current === 'plan' ? 19 : 18,
      pitch: initialCameraModeRef.current === 'plan' ? 0 : 62,
      bearing: initialCameraModeRef.current === 'plan' ? 0 : -20,
      maxPitch: 80,
      canvasContextAttributes: { antialias: true },
      transformRequest: (url) => (
        url.includes('/gis/evidence/') && accessToken
          ? { url, headers: { Authorization: `Bearer ${accessToken}` } }
          : { url }
      ),
    })
    map.addControl(new maplibregl.NavigationControl({ visualizePitch: true }), 'top-left')
    map.addControl(new maplibregl.ScaleControl({ maxWidth: 120, unit: 'metric' }), 'bottom-left')
    map.on('click', (event) => {
      propsRef.current.onPick(event.lngLat.lat, event.lngLat.lng)
    })
    map.on('load', () => setMessage(''))
    map.on('moveend', () => setViewZoom(map.getZoom()))
    map.on('error', (event) => {
      const errorMessage = event.error?.message
      if (errorMessage?.includes('tile.openstreetmap.org')
        || errorMessage?.includes('server.arcgisonline.com')
        || errorMessage?.includes('portal.spatial.nsw.gov.au')) return
      if (errorMessage) setMessage(errorMessage)
    })
    mapRef.current = map
    return () => {
      mapRef.current = null
      map.remove()
    }
  }, [accessToken])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      map.setLayoutProperty('street', 'visibility', props.baseMapMode === 'street' ? 'visible' : 'none')
      map.setLayoutProperty('satellite', 'visibility', props.baseMapMode === 'satellite' ? 'visible' : 'none')
      map.setLayoutProperty('nsw', 'visibility', props.baseMapMode === 'nsw' ? 'visible' : 'none')
    }
    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [props.baseMapMode])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    if (props.cameraMode === 'plan') {
      map.easeTo({ pitch: 0, bearing: 0, zoom: Math.max(map.getZoom(), 19), duration: 450 })
    } else {
      map.easeTo({ pitch: 62, bearing: -20, zoom: Math.min(map.getZoom(), 19), duration: 450 })
    }
  }, [props.cameraMode])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      const data = props.siteBoundary
        ? { type: 'FeatureCollection', features: [props.siteBoundary.feature] }
        : { type: 'FeatureCollection', features: [] }
      if (!map.getSource('site-boundary')) {
        map.addSource('site-boundary', { type: 'geojson', data: data as any })
        map.addLayer({
          id: 'site-boundary-fill', type: 'fill', source: 'site-boundary',
          paint: { 'fill-color': '#f59e0b', 'fill-opacity': 0.08 },
        })
        map.addLayer({
          id: 'site-boundary-line', type: 'line', source: 'site-boundary',
          paint: { 'line-color': '#fbbf24', 'line-width': 2, 'line-dasharray': [3, 2] },
        })
      } else {
        (map.getSource('site-boundary') as maplibregl.GeoJSONSource).setData(data as any)
      }
    }
    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [props.siteBoundary])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      const includedIds = new Set(
        Object.values(props.directionalEvidence?.directions ?? {})
          .flatMap((direction) => direction.shielding_building_ids),
      )
      const features = (props.buildingEvidence?.features ?? []).map((feature) => ({
        type: 'Feature',
        properties: {
          source_id: feature.source_id,
          height: feature.height_m ?? 3,
          height_lower_m: feature.height_lower_m,
          height_upper_m: feature.height_upper_m,
          height_method: feature.height_observations?.[0]?.method ?? 'source estimate',
          included: includedIds.has(feature.source_id),
          confidence: feature.confidence,
          outline_source: feature.outline_source ?? 'unknown',
          height_source: feature.height_source ?? 'unknown',
        },
        geometry: feature.geometry,
      }))
      const data = { type: 'FeatureCollection', features }
      if (!map.getSource('shielding-buildings')) {
        map.addSource('shielding-buildings', { type: 'geojson', data: data as any })
        map.addLayer({
          id: 'shielding-building-volumes', type: 'fill-extrusion', source: 'shielding-buildings',
          paint: {
            'fill-extrusion-color': [
              'case',
              ['get', 'included'], '#10b981',
              ['match', ['get', 'outline_source'],
                'OpenStreetMap', '#22d3ee',
                'Microsoft ML Buildings', '#facc15',
                '#a78bfa'],
            ],
            'fill-extrusion-height': ['get', 'height'],
            'fill-extrusion-base': 0,
            'fill-extrusion-opacity': ['case', ['get', 'included'], 0.55, 0.20],
          },
        })
        map.addLayer({
          id: 'shielding-building-lines', type: 'line', source: 'shielding-buildings',
          paint: {
            'line-color': [
              'case',
              ['get', 'included'], '#6ee7b7',
              ['match', ['get', 'outline_source'],
                'OpenStreetMap', '#67e8f9',
                'Microsoft ML Buildings', '#fde047',
                '#c4b5fd'],
            ],
            'line-width': ['case', ['get', 'included'], 2, 1],
          },
        })
      } else {
        (map.getSource('shielding-buildings') as maplibregl.GeoJSONSource).setData(data as any)
      }
    }
    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [props.buildingEvidence, props.directionalEvidence])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    const apply = () => {
      const current = propsRef.current
      const data = siteFeatures(current)
      if (!map.getSource('cardinal-sectors')) {
        map.addSource('cardinal-sectors', { type: 'geojson', data: data.sectors as any })
        map.addLayer({
          id: 'cardinal-sector-fill', type: 'fill', source: 'cardinal-sectors',
          paint: {
            'fill-color': ['case', ['>=', ['get', 'multiplier'], 1], '#be123c', '#0369a1'],
            'fill-opacity': 0.18,
          },
        })
        map.addLayer({
          id: 'cardinal-sector-line', type: 'line', source: 'cardinal-sectors',
          paint: { 'line-color': '#7dd3fc', 'line-width': 1 },
        })
      } else {
        (map.getSource('cardinal-sectors') as maplibregl.GeoJSONSource).setData(data.sectors as any)
      }
      if (!map.getSource('site-structure')) {
        map.addSource('site-structure', { type: 'geojson', data: data.structure as any })
        map.addLayer({
          id: 'site-structure-volume', type: 'fill-extrusion', source: 'site-structure',
          filter: ['==', '$type', 'Polygon'],
          paint: {
            'fill-extrusion-color': '#14b8a6',
            'fill-extrusion-height': ['get', 'height'],
            'fill-extrusion-opacity': 0.82,
          },
        })
        map.addLayer({
          id: 'site-structure-front', type: 'line', source: 'site-structure',
          filter: ['==', ['get', 'kind'], 'front'],
          paint: { 'line-color': '#fbbf24', 'line-width': 5 },
        })
      } else {
        (map.getSource('site-structure') as maplibregl.GeoJSONSource).setData(data.structure as any)
      }
      map.setLayoutProperty(
        'site-structure-volume',
        'visibility',
        map.getLayer('candidate-design-model') ? 'none' : 'visible',
      )
      map.easeTo({ center: [current.longitude, current.latitude], duration: 350 })
    }
    if (map.isStyleLoaded()) apply()
    else map.once('load', apply)
  }, [
    props.cardinalMultipliers,
    props.footprintLengthM,
    props.footprintWidthM,
    props.frontBearingDegrees,
    props.latitude,
    props.longitude,
    props.referenceHeightM,
  ])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    let cancelled = false
    let loadedLayer: Awaited<ReturnType<typeof loadCandidateDesignLayer>> | null = null
    const layerId = 'candidate-design-model'
    const apply = async () => {
      if (cancelled || mapRef.current !== map) return
      if (map.getLayer(layerId)) map.removeLayer(layerId)
      if (map.getLayer('site-structure-volume')) {
        map.setLayoutProperty('site-structure-volume', 'visibility', 'visible')
      }
      if (!props.candidateModelUrl) {
        setCandidateUpAxis(null)
        setCandidateSizeM(null)
        return
      }
      setMessage('Loading active candidate design…')
      try {
        const loaded = await loadCandidateDesignLayer({
          id: layerId,
          map,
          modelUrl: props.candidateModelUrl,
          getAccessToken: props.getAccessToken,
          footprintLengthM: props.footprintLengthM,
          footprintWidthM: props.footprintWidthM,
          representation: props.candidateRepresentation,
          getPlacement: () => {
            const current = propsRef.current
            return {
              longitude: current.longitude,
              latitude: current.latitude,
              frontBearingDegrees: current.frontBearingDegrees,
            }
          },
        })
        if (cancelled) {
          loaded.dispose()
          return
        }
        loadedLayer = loaded
        map.addLayer(loaded.layer)
        if (map.getLayer('site-structure-volume')) {
          map.setLayoutProperty('site-structure-volume', 'visibility', 'none')
        }
        setCandidateUpAxis(loaded.upAxis)
        setCandidateSizeM([loaded.sizeM.x, loaded.sizeM.y, loaded.sizeM.z])
        setMessage('')
      } catch (error) {
        if (!cancelled) {
          setMessage(error instanceof Error
            ? `${error.message}; showing the verified footprint instead.`
            : 'Candidate model failed; showing the verified footprint instead.')
        }
      }
    }
    const onLoad = () => void apply()
    if (map.isStyleLoaded()) void apply()
    else map.once('load', onLoad)
    return () => {
      cancelled = true
      map.off('load', onLoad)
      if (mapRef.current === map) {
        if (map.getLayer(layerId)) map.removeLayer(layerId)
        else loadedLayer?.dispose()
        if (map.getLayer('site-structure-volume')) {
          map.setLayoutProperty('site-structure-volume', 'visibility', 'visible')
        }
      } else {
        loadedLayer?.dispose()
      }
    }
  }, [
    props.candidateModelUrl,
    props.candidateRepresentation,
    props.footprintLengthM,
    props.footprintWidthM,
    props.getAccessToken,
  ])

  useEffect(() => {
    mapRef.current?.triggerRepaint()
  }, [props.frontBearingDegrees, props.latitude, props.longitude])

  useEffect(() => {
    const map = mapRef.current
    if (!map) return
    let cancelled = false
    const apply = async () => {
      if (props.overlayMode === 'wind') {
        try {
          const response = await apiFetch(`${props.serverUrl}/wind/regions.geojson`, props.getAccessToken)
          if (!response.ok) throw new Error(`Wind overlay returned ${response.status}`)
          const data = await response.json()
          if (cancelled) return
          if (!map.getSource('wind-regions')) {
            map.addSource('wind-regions', { type: 'geojson', data })
            map.addLayer({
              id: 'wind-region-fill', type: 'fill', source: 'wind-regions',
              paint: {
                'fill-color': ['match', ['get', 'region'],
                  'C', '#ef4444', 'D', '#991b1b', 'B1', '#f59e0b', 'B2', '#f59e0b', '#22c55e'],
                'fill-opacity': 0.24,
              },
            })
            map.addLayer({
              id: 'wind-region-line', type: 'line', source: 'wind-regions',
              paint: { 'line-color': '#f8fafc', 'line-width': 1 },
            })
          }
          map.setLayoutProperty('wind-region-fill', 'visibility', 'visible')
          map.setLayoutProperty('wind-region-line', 'visibility', 'visible')
        } catch (error) {
          if (!cancelled) setMessage(error instanceof Error ? error.message : 'Wind overlay failed')
        }
      } else if (map.getLayer('wind-region-fill')) {
        map.setLayoutProperty('wind-region-fill', 'visibility', 'none')
        map.setLayoutProperty('wind-region-line', 'visibility', 'none')
      }

      if (map.getSource('site-terrain')) {
        map.setTerrain(null)
        if (map.getLayer('site-hillshade')) map.removeLayer('site-hillshade')
        map.removeSource('site-terrain')
      }
      const useTerrainSource = Boolean(
        props.terrainEvidenceId
        && (props.terrainMode === 'terrain' || props.overlayMode === 'terrain'),
      )
      if (useTerrainSource && props.terrainEvidenceId) {
        map.addSource('site-terrain', {
          type: 'raster-dem',
          tiles: [`${props.serverUrl}/gis/evidence/${props.terrainEvidenceId}/terrain-rgb/{z}/{x}/{y}.png`],
          tileSize: 256,
          encoding: 'terrarium',
          minzoom: 12,
          maxzoom: 18,
          ...(props.terrainEvidenceBounds ? { bounds: props.terrainEvidenceBounds } : {}),
        })
        if (props.terrainMode === 'terrain') {
          // Keep engineering geometry at the cached raster's true vertical
          // scale. The satellite/street raster and evidence layers are then
          // draped over the DEM by MapLibre.
          map.setTerrain({ source: 'site-terrain', exaggeration: 1 })
        }
        if (props.overlayMode === 'terrain') {
          const beforeLayer = map.getLayer('cardinal-sector-fill')
            ? 'cardinal-sector-fill'
            : undefined
          map.addLayer({
            id: 'site-hillshade', type: 'hillshade', source: 'site-terrain',
            paint: { 'hillshade-exaggeration': 0.35 },
          }, beforeLayer)
        }
      }
    }
    if (map.isStyleLoaded()) void apply()
    else map.once('load', () => void apply())
    return () => {
      cancelled = true
    }
  }, [
    props.getAccessToken,
    props.overlayMode,
    props.serverUrl,
    props.terrainEvidenceBounds,
    props.terrainEvidenceId,
    props.terrainMode,
  ])

  return (
    <div className="relative h-full min-h-[28rem] w-full overflow-hidden rounded border border-slate-700 bg-slate-900">
      <div ref={divRef} className="h-full w-full" />
      {message && (
        <div className="absolute left-3 right-3 top-3 rounded border border-amber-500/40 bg-slate-950/90 p-2 text-xs text-amber-200">
          {message}
        </div>
      )}
      <button
        type="button"
        className="absolute right-3 top-3 rounded border border-slate-600 bg-slate-950/85 px-2 py-1 text-[10px] font-semibold text-slate-200 hover:border-cyan-400"
        onClick={() => {
          const current = propsRef.current
          mapRef.current?.easeTo({
            center: [current.longitude, current.latitude],
            zoom: 18,
            pitch: 62,
            duration: 500,
          })
        }}
      >
        Frame site
      </button>
      <div className="pointer-events-none absolute bottom-2 left-2 rounded bg-slate-950/80 px-2 py-1 text-[9px] text-slate-300">
        {props.terrainMode === 'terrain' ? 'cached DEM ground · 1× elevation' : 'flat ground'}
        {` · z${viewZoom.toFixed(1)}`}
        {candidateUpAxis ? ` · candidate ${candidateUpAxis.toUpperCase()}-up` : ''}
        {candidateUpAxis ? ` · ${props.candidateRepresentation}` : ''}
        {candidateSizeM
          ? ` · ${candidateSizeM.map((value) => value.toFixed(1)).join(' × ')} m`
          : ''}
        {' · click to position the structure'}
      </div>
    </div>
  )
}
