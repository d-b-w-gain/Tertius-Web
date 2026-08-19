# README hero tooling

This folder contains the deterministic compositor used to build `assets/hero-product.png`. It turns a real Tertius capture into a mid-century engineering-report composition: warm paper stock, black technical linework, signal-red authored geometry, genuine Gorton lettering, a workflow/output table, and restrained print imperfections.

## Inputs

- `assets/extus-viewer.png`: clean 1600 × 900 product capture.
- `GortonClassicRegular.otf`: supplied locally when regenerating; the licensed font is not stored in this repository.
- Python packages: `Pillow` and `fontTools`.

## Regenerate the hero

Run from the repository root:

```powershell
python assets/readme/compose-hero.py `
  --input assets/extus-viewer.png `
  --output assets/hero-product.png `
  --font "W:\ben\ContextUI\default\workflows\gainengineering\GainEngineeringWebsite\fonts\GortonClassicRegular.otf"
```

The script reads the Gorton font's open glyph geometry with `fontTools` and strokes it directly. Do not render the wordmark as ordinary filled font text and do not trace a rasterised logo; both lose the single-stroke engraving construction.

The paper grain, fold, ink registration, duotone conversion, typography, labels, and layout are deterministic. Re-running the same command with the same inputs produces the same hero.

After changing the composition, inspect the PNG at full size and run:

```powershell
git diff --check
```
