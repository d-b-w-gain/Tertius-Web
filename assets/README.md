# README image capture brief

README images must be evidence of the real product. Do not use generated CAD imagery, invented controls, synthetic interface text, or decorative AI artwork.

## Hero

The final hero should be a staged, authenticated Tertius session at a 16:9 aspect ratio:

- Use a real project with visually legible structural geometry.
- Open **Generate Design** with two or three genuine design iterations visible.
- Keep the compiled model prominent in the shared Extus viewport.
- Include enough application navigation to identify the integrated workbench.
- Exclude browser chrome, credentials, tenant identifiers, provider details, prompts containing private information, and transient error states.
- Keep any overlay copy restrained and factual; the README remains the source of truth for the full product description.

Capture at 1600 × 900 or larger and export the final optimized image as `hero-product.webp` or `hero-product.png`. The current `hero-product.png` is a real authenticated Extus session captured after the shed model finished loading.

The checked-in hero uses a deterministic Pillow composition in [`scripts/compose-readme-hero.py`](../scripts/compose-readme-hero.py): a real Extus capture, a restrained gradient for contrast, and a small amount of editorial type. This keeps the process open-source and repeatable without requiring a design application. A licensed Gorton Classic font can be supplied at capture time with `--font`; the font itself does not need to be redistributed. The script extracts its open glyph geometry with `fontTools` and strokes the paths with round caps and joins—normal filled-text rasterisation does not reproduce this single-stroke engraving font correctly.

## Supporting views

Capture three outcome-oriented images from the same project and runtime:

1. **Design and iterate:** Generate Design conversation beside the compiled model.
2. **Inspect and procure:** Procurement with a selected visual component, its BoM row, and the model selection visible together.
3. **Compile and document:** Intus compile state or Timus drawing output using the same project.

Use the same viewport size, project, colour treatment, and browser zoom for the complete set. Prefer 16:9 captures with the browser chrome removed. Keep each optimized image below approximately 1 MB where legibility permits.

## Validation

Use an isolated authenticated runtime so the capture represents the complete product:

```bash
RELEASE_NAME=tertius-readme-capture \
UI_LOCAL_PORT=18083 \
API_LOCAL_PORT=18003 \
METRICS_LOCAL_PORT=8430 \
TRACES_LOCAL_PORT=10431 \
KEDA_ENABLED=true \
scripts/harness-k3s.sh up

RELEASE_NAME=tertius-readme-capture \
UI_LOCAL_PORT=18083 \
API_LOCAL_PORT=18003 \
scripts/harness-k3s.sh live-flow
```

Before committing, render the root README at normal GitHub width and confirm that the model, conversation, BoM rows, and drawing remain readable when scaled down.
