# Part drawings

Fabrication drawings of single parts, declared in `design.py` and produced by
the drafting build. This is the Tertius equivalent of driving a SolidWorks
drawing from a part script: named views, dimensions with real values, notes
and a title block, rendered on the existing lossless (OCC hidden-line) PDF
path. The drafting preview is unchanged.

## Declaring a drawing

```python
import build123d as bd
from tertius.drawing import Drawing

drw = Drawing(
    "Mount_Angle", angle,                 # name (unique per compile), part shape
    sheet="A3", scale=1, projection="third",
    title="RolaCase mount angle", number="NGZ-D4Y4-CH3ZG",
    material="Mild steel", finish="Zinc plate", revision="A",
    drawn="Dan", date="2026-11-03", reference="baseline 3X5-BL01",
    notes=["CUT LENGTH 180 +/-0.5. DEBURR ALL EDGES."],
)
front = drw.view("front", at=(300, 185))      # sheet mm, view centre
side = drw.view("left", aligned_to=front)     # projected: shares scale and an axis
drw.view("iso", at=(355, 248), scale=0.5)     # its own scale, labelled "SCALE 1:2"

side.horizontal((0, 0, 0), (0, -22.5, 19.25), offset=-6)   # model points, mm
side.vertical((0, 0, 0), (0, -157.5, 19.25), offset=8)
side.diameter(center=(0, -22.5, 19.25), diameter=4.2, angle=60)
front.horizontal((0, 0, 0), (50, 0, 0), offset=-10)
```

* **Views**: `front`, `back`, `left`, `right`, `top`, `bottom`, `iso` (Z up,
  front faces -Y), or any name with `direction=` (toward the viewer) and
  `up=`. A view placed with `aligned_to=` must be an orthographic projection
  of its parent; it goes on the side the projection angle dictates (third
  angle: right view to the right, top view above), 25 mm clear unless `at=`
  gives the free coordinate. Hidden lines: `hidden=True`.
* **Dimensions** (on a view): `horizontal`, `vertical`, `aligned`,
  `linear(axis="auto")`, `diameter`, `radius`, `angle(vertex, p1, p2,
  radius=)` (seen true in the view; a short ray is drawn as a reference line
  from the vertex), and `ordinate(origin, points, axis=, offset=)`: every
  point's distance from a datum, one value per position on a common baseline,
  jogged apart where values are too close to read. Ordinates are the way to
  locate many holes and corners from one datum without a stack of dimensions. Points are model-space
  `(x, y, z)` tuples, Vectors or Vertices; circles are a circular `bd.Edge`
  or `center=` + `diameter=`/`radius=` (+ `normal=`, default the view
  direction). Values are measured from the model, formatted to `decimals`
  (drawing default 2); `text=`, `prefix=` and `suffix=` override or decorate.
* **Offsets** are sheet mm: horizontal dimensions below (negative) or above
  (positive) the view outline, vertical ones left or right of it, aligned ones
  perpendicular to the measured line.
* A drawing must be declared at the top level of the design run (not inside a
  cached `@part` builder), so it is registered on every compile.

## Building and fetching

The drawings are projected in the ordinary drafting build (`timus_views`),
beside the four fixed drafting views, and stored in the same artifact under
`drawings` (`tertius.part-drawing.v1`, sheet millimetres, origin bottom-left).

| Method & path (under `/api/timus`) | Returns |
|---|---|
| `POST /projects/{name}/drafting/build` | queues the build (existing) |
| `GET /projects/{name}/drafting/status` | `ready` when done (existing) |
| `GET /projects/{name}/drawings` | `{drawings: [{name, title, number, sheet, views, dimensions, error}]}` |
| `GET /projects/{name}/drawings/{drawing}.json` | the resolved drawing: views, entities, every dimension's value and text |
| `GET /projects/{name}/drawings/{drawing}.pdf` | the sheet as a PDF |

All three return 409 when the build is older than `design.py`. A drawing that
cannot be laid out (for example a diameter on a circle seen edge-on) is kept
with its `error`; its PDF/JSON return 422 with the message, and the rest of the
build is unaffected.

## Where the code is

* `tertius/drawing.py`: author API and `project_drawing()` (exact HLR per
  view on the drawing's own part, placement, dimension resolution).
* `server/core/compile_sandbox.py` (`timus_views`): projects declared drawings.
* `server/workflows/timus/part_drawing_pdf.py`: PDF rendering (ISO 5457
  border and zones, ISO 129 style dimensions, projection symbol, title block).
* `server/workflows/timus/timus_server.py`: the `drawings` endpoints.
* `server/tests/test_part_drawings.py`.

## Not yet

Section views and hatching, detail views, hole tables, dimensions recorded
while modelling (imported into
views automatically), and showing the sheets in the drafting workbench UI.
