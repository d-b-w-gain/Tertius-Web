import { describe, expect, it } from 'vitest';
import * as THREE from 'three';
import {
  buildSupplierQuoteHtml,
  buildSupplierQuoteCsv,
  canonicalRequirementKey,
  deriveAssemblyTreeManifest,
  groupManifestRequirements,
  manifestCounts,
  normalizeManifestEnvelope,
  normalizeProcurementManifest,
  priceGroupedLine,
  quoteFocusForLine,
  resolveBomArtifactState,
} from './BomReviewTab';
import type { BomManifest, ManifestEnvelope, SupplierPricing } from './BomReviewTab';

const envelope = (manifest: BomManifest, matchesModel = true, artifactState?: ManifestEnvelope['artifact_state']): ManifestEnvelope => ({
  manifest,
  manifest_artifact_id: 'manifest-a',
  manifest_compile_job_id: matchesModel ? 'job-a' : 'job-old',
  model_artifact_id: 'model-a',
  model_compile_job_id: 'job-a',
  matches_model: matchesModel,
  artifact_state: artifactState,
  manifest_counts: manifestCounts(manifest),
  mtime: 42,
});

describe('Procurement manifest grouping', () => {
  it('groups repeated explicit requirements by structured identity', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [{ id: 'portal', label: 'Portal', parent_id: null }],
      components: [
        { id: 'portal.column.left', scope_id: 'portal', label: 'Left column', role: 'Column', visual_node_ids: ['bom:portal.column.left:Column'] },
        { id: 'portal.column.right', scope_id: 'portal', label: 'Right column', role: 'Column', visual_node_ids: ['bom:portal.column.right:Column'] },
      ],
      requirements: [
        { id: 'r1', component_id: 'portal.column.left', part_number: 'C10019', quantity: 1, unit: 'each', dimensions: { length_mm: 2400 } },
        { id: 'r2', component_id: 'portal.column.right', part_number: 'C10019', quantity: 1, unit: 'each', dimensions: { length_mm: 2400 } },
      ],
      diagnostics: [],
    };

    const grouped = groupManifestRequirements(manifest, 'portal');

    expect(grouped).toHaveLength(1);
    expect(grouped[0]?.displayName).toBe('C10019x24');
    expect(grouped[0]?.quantity).toBe(2);
    expect(grouped[0]?.componentIds).toEqual(['portal.column.left', 'portal.column.right']);
    expect(canonicalRequirementKey(manifest.requirements[0]!)).toContain('length_mm=2400');
  });

  it('does not create rows when the manifest has no explicit requirements', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [],
      components: [],
      requirements: [],
      diagnostics: [{ code: 'no_bom_metadata', severity: 'error', message: 'missing' }],
    };

    expect(groupManifestRequirements(manifest, '__all__')).toEqual([]);
  });

  it('keeps non-orderable diagnostic rows out of grouped purchase quantities', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [{ id: 'foundation', label: 'Foundation', parent_id: null }],
      components: [
        { id: 'foundation.rebar', scope_id: 'foundation', label: 'Rebar', role: 'component', visual_node_ids: ['rebar-node'] },
      ],
      requirements: [
        {
          id: 'foundation.rebar.requirement',
          component_id: 'foundation.rebar',
          part_number: 'REBAR-D16',
          quantity: 1,
          rolled_up_quantity: 12,
          quantity_source: 'diagnostic_placeholder',
          orderable: false,
          unit: 'each',
          dimensions: {},
        },
      ],
      diagnostics: [],
    };

    const grouped = groupManifestRequirements(manifest, 'foundation');

    expect(grouped).toHaveLength(1);
    expect(grouped[0]?.partNumber).toBe('REBAR-D16');
    expect(grouped[0]?.quantity).toBe(0);
    expect(grouped[0]?.status).toBe('incomplete');
  });

  it('keeps placeholder part numbers in grouped totals but marks them incomplete', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [{ id: 'floor', label: 'Floor', parent_id: null }],
      components: [
        { id: 'floor.screw.a', scope_id: 'floor', label: 'Floor Screw', role: 'component', visual_node_ids: ['screw-a'] },
        { id: 'floor.screw.b', scope_id: 'floor', label: 'Floor Screw', role: 'component', visual_node_ids: ['screw-b'] },
      ],
      requirements: [
        {
          id: 'floor.screw.a.requirement',
          component_id: 'floor.screw.a',
          part_number: 'FS-FLOOR-SCREW-A',
          part_number_placeholder: true,
          quantity: 1,
          quantity_source: 'visual_instances',
          orderable: true,
          unit: 'each',
          dimensions: {},
        },
        {
          id: 'floor.screw.b.requirement',
          component_id: 'floor.screw.b',
          part_number: 'FS-FLOOR-SCREW-A',
          part_number_placeholder: true,
          quantity: 1,
          quantity_source: 'visual_instances',
          orderable: true,
          unit: 'each',
          dimensions: {},
        },
      ],
      diagnostics: [],
    };

    const grouped = groupManifestRequirements(manifest, 'floor');

    expect(grouped).toHaveLength(1);
    expect(grouped[0]?.partNumber).toBe('FS-FLOOR-SCREW-A');
    expect(grouped[0]?.quantity).toBe(2);
    expect(grouped[0]?.status).toBe('incomplete');
  });

  it('classifies missing and diagnostic-only manifests as not ready', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [],
      components: [],
      requirements: [],
      diagnostics: [{ code: 'no_bom_metadata', severity: 'error', message: 'missing' }],
    };

    expect(resolveBomArtifactState(null)).toBe('missing_manifest');
    expect(resolveBomArtifactState(envelope(manifest))).toBe('diagnostic_only');
    expect(manifestCounts(manifest)).toEqual({ scopes: 0, components: 0, requirements: 0, diagnostics: 1 });
  });

  it('classifies scopes-only, ready, and stale manifests separately', () => {
    const scopesOnly: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [{ id: 'portal', label: 'Portal', parent_id: null }],
      components: [],
      requirements: [],
      diagnostics: [],
    };
    const ready: BomManifest = {
      ...scopesOnly,
      components: [{ id: 'portal.column.left', scope_id: 'portal', label: 'Left column', role: 'Column', visual_node_ids: ['bom:portal.column.left:Column'] }],
      requirements: [{ id: 'r1', component_id: 'portal.column.left', part_number: 'C10019', quantity: 1, unit: 'each', dimensions: { length_mm: 2400 } }],
    };

    expect(resolveBomArtifactState(envelope(scopesOnly))).toBe('scopes_only');
    expect(resolveBomArtifactState(envelope(ready))).toBe('ready');
    expect(resolveBomArtifactState(envelope(ready, false))).toBe('stale_manifest');
    expect(resolveBomArtifactState(envelope(scopesOnly, true, 'diagnostic_only'))).toBe('diagnostic_only');
  });

  it('derives draft components from named GLTF leaf groups under assembly groups', () => {
    const root = new THREE.Object3D();
    const portal = new THREE.Object3D();
    portal.name = 'Portal_1';
    const leftColumn = new THREE.Object3D();
    leftColumn.name = 'Left_Column';
    leftColumn.add(new THREE.Mesh(new THREE.BoxGeometry(1, 1, 1), new THREE.MeshBasicMaterial()));
    portal.add(leftColumn);
    root.add(portal);

    const manifest = deriveAssemblyTreeManifest(
      root,
      {
        version: 1,
        source_snapshot_hash: 'snapshot-a',
        scopes: [],
        components: [],
        requirements: [],
        diagnostics: [],
      },
      {
        calls: [{
          function: 'lysaght_zc_purlin',
          sourceFile: 'design.py',
          scope: 'make_portal::column',
          line: 240,
          parameters: {},
          standardInputs: {
            part_number: { kind: 'reference', name: 'PURLIN_PART_NUMBER' },
            length_mm: { kind: 'reference', name: 'column_height' },
          },
          bomKind: 'structural_member',
          bomReadiness: 'ok',
          bomMissingFields: [],
        }],
      },
      [
        { name: 'PURLIN_PART_NUMBER', value: 'C10019' },
        { name: 'column_height', value: 2400 },
      ],
    );

    expect(manifest?.scopes.map((scope) => scope.label)).toEqual(['Portal_1']);
    expect(manifest?.components.map((component) => component.label)).toEqual(['Left_Column']);
    expect(manifest?.requirements[0]?.part_number).toBe('C10019');
    expect(manifest?.requirements[0]?.dimensions).toEqual({ length_mm: 2400 });
    expect(groupManifestRequirements(manifest, manifest?.scopes[0]?.id || '__all__')[0]?.displayName).toBe('C10019x24');
  });

  it('calculates generic box packaging from grouped requirements', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [],
      components: [{ id: 'bolts', scope_id: null, label: 'Bolts', role: 'Fastener', visual_node_ids: ['bom:bolts:Fastener'] }],
      requirements: [{ id: 'r1', component_id: 'bolts', part_number: 'M12_BOLT', quantity: 84, unit: 'each', dimensions: {} }],
      diagnostics: [],
    };
    const pricing: SupplierPricing = {
      section: 'M12_BOLT',
      package_type: 'box',
      stock_lengths: [],
      package_quantity: 100,
      package_unit: 'each',
      price_per_package: 55,
      price_per_unit: 0,
      notes: '',
    };
    const line = groupManifestRequirements(manifest, '__all__')[0]!;

    const priced = priceGroupedLine(line, pricing, 'supplier-a');

    expect(priced.buy_quantity).toBe(1);
    expect(priced.total_cost).toBe(55);
    expect(priced.waste_quantity).toBe(16);
  });

  it('adapts procurement analysis artifacts into grouped BoM rows', () => {
    const manifest = normalizeProcurementManifest({
      version: 1,
      source: 'source_only_analysis',
      assemblies: [{ id: 'roof-cladding', label: 'Roof Cladding', parent_id: null, path: 'roof_cladding' }],
      components: [{
        id: 'roof-cladding-make-custom-orb-roof-sheet-430',
        label: 'make_custom_orb_roof_sheet',
        assembly_id: 'roof-cladding',
        visual_node_ids: [],
        source_trace: { function: 'make_custom_orb_roof_sheet', source_file: 'design.py', source_line: 430 },
      }],
      requirements: [{
        id: 'roof-cladding-make-custom-orb-roof-sheet-430.requirement',
        component_id: 'roof-cladding-make-custom-orb-roof-sheet-430',
        assembly_id: 'roof-cladding',
        part_number: 'CUSTOM-ORB',
        quantity: 1,
        rolled_up_quantity: 14,
        quantity_source: 'explicit',
        quantity_confidence: 'verified',
        unit: 'sheet',
        dimensions: { length_mm: 2800 },
        material: 'steel',
        finish: 'zincalume',
      }],
      diagnostics: [],
    });

    expect(manifestCounts(manifest)).toEqual({ scopes: 1, components: 1, requirements: 1, diagnostics: 0 });

    const grouped = groupManifestRequirements(manifest, 'roof-cladding');
    expect(grouped).toHaveLength(1);
    expect(grouped[0]?.displayName).toBe('CUSTOM-ORBx28');
    expect(grouped[0]?.quantity).toBe(1);
    expect(grouped[0]?.unit).toBe('sheet');
    expect(groupManifestRequirements(manifest, '__all__')[0]?.quantity).toBe(14);
    expect(groupManifestRequirements(manifest, 'missing-scope')).toEqual([]);
  });

  it('hides component-label-only fallback rows and cleans generated assembly labels', () => {
    const manifest = normalizeProcurementManifest({
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [{ id: 'shed-building-sh01', label: 'Shed_Building_SH01-51-31-24-20', parent_id: null }],
      components: [{
        id: 'pf01-left-column',
        scope_id: 'shed-building-sh01',
        label: 'PF01_Left_Column',
        role: 'component',
        visual_node_ids: ['pf01-left-column'],
      }],
      requirements: [{
        id: 'pf01-left-column.requirement',
        component_id: 'pf01-left-column',
        scope_id: 'shed-building-sh01',
        part_number: null,
        quantity: 1,
        unit: 'each',
        dimensions: { component_label: 'PF01_Left_Column' },
      }],
      diagnostics: [],
    });

    expect(manifest?.scopes[0]?.label).toBe('Shed Building');
    expect(manifestCounts(manifest)).toEqual({ scopes: 1, components: 1, requirements: 0, diagnostics: 0 });
    expect(groupManifestRequirements(manifest, 'shed-building-sh01')).toEqual([]);
  });

  it('normalizes procurement analysis envelopes before resolving artifact state', () => {
    const normalized = normalizeManifestEnvelope({
      manifest: {
        version: 1,
        source: 'source_only_analysis',
        assemblies: [{ id: 'make-tower', label: 'Tower', parent_id: null }],
        components: [],
        requirements: [{
          id: 'tower-fasteners.bolt',
          component_id: 'tower-fasteners',
          assembly_id: 'make-tower',
          part_number: 'DIN-6921-M16X50',
          rolled_up_quantity: 528,
          unit: 'each',
          dimensions: { size: 'M16', length_mm: 50 },
        }],
        diagnostics: [],
      } as unknown as BomManifest,
      manifest_artifact_id: 'analysis-a',
      manifest_compile_job_id: 'job-a',
      model_artifact_id: 'model-a',
      model_compile_job_id: 'job-a',
      matches_model: true,
      mtime: 123,
    });

    expect(resolveBomArtifactState(normalized)).toBe('ready');
    expect(normalized?.manifest_counts).toEqual({ scopes: 1, components: 1, requirements: 1, diagnostics: 0 });
    expect(groupManifestRequirements(normalized?.manifest || null, '__all__')[0]?.quantity).toBe(528);
  });

  it('builds a supplier quote CSV with tactful competitive-pricing wording', () => {
    const manifest: BomManifest = {
      version: 1,
      source_snapshot_hash: 'snapshot-a',
      scopes: [],
      components: [
        { id: 'purlin', scope_id: null, label: 'Purlin', role: 'Structural', visual_node_ids: ['purlin-node'] },
        { id: 'bolt', scope_id: null, label: 'Bolt', role: 'Fastener', visual_node_ids: ['bolt-node'] },
      ],
      requirements: [
        { id: 'r1', component_id: 'purlin', part_number: 'C10012', quantity: 1, unit: 'each', dimensions: { length_mm: 9000 }, material: 'galvanised steel' },
        { id: 'r2', component_id: 'bolt', part_number: 'M12_BOLT', quantity: 84, unit: 'each', dimensions: { size: 'M12' } },
      ],
      diagnostics: [],
    };
    const lines = groupManifestRequirements(manifest, '__all__');

    expect(quoteFocusForLine(lines.find((line) => line.partNumber === 'C10012')!)).toContain('Bulk/long-length');
    expect(quoteFocusForLine(lines.find((line) => line.partNumber === 'M12_BOLT')!)).toContain('feel welcome to quote if convenient');

    const csv = buildSupplierQuoteCsv(lines, { projectName: 'Shed', scopeLabel: 'Whole design', snapshotHash: 'snapshot-a' });
    const html = buildSupplierQuoteHtml(lines, { projectName: 'Shed', scopeLabel: 'Whole design', snapshotHash: 'snapshot-a' });

    expect(csv).toContain('Please quote the line items that suit your normal supply range');
    expect(csv).toContain('"Bulk steel / roofing"');
    expect(csv).toContain('"Small hardware / general"');
    expect(csv).toContain('"Supplier unit price ex GST"');
    expect(csv).toContain('"C10012"');
    expect(csv).toContain('"9000"');
    expect(html).toContain('Shed BoM Quote Request');
    expect(html).toContain('Unit $ ex GST');
    expect(html).toContain('Notes / substitutions');
    expect(html).toContain('Optional small hardware');
  });
});
