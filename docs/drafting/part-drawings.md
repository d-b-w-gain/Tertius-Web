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

## Drawing sets

Several sheets issued together (a plan split over two sheets plus a schedule,
say) are a **drawing set**: one multi-page PDF, each title block reading
SHEET n OF m.

```python
from tertius.drawing import Drawing, DrawingSet

pack = DrawingSet("Drain_Holes", title="Drain auger holes", number="WGB-D-01")
back = Drawing("Drain_Holes_1_Back", plan_back, sheet="A3", scale=1/75, drawing_set=pack)
drive = Drawing("Drain_Holes_2_Driveway", plan_drive, sheet="A3", scale=1/75)
pack.add(drive)                                # same as drawing_set=pack
```

* Sheet order is the order the drawings join the set. A drawing belongs to at
  most one set; set names are unique per compile, like drawing names.
* Every sheet is still an ordinary drawing (its own JSON and PDF endpoints).
  The set only adds the numbering and the combined PDF. An explicit
  `fields={"sheet_no": ...}` on a drawing wins over the automatic number.
* Sheets in a set may use different sizes; each page keeps its own.

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
| `GET /projects/{name}/drawing-sets` | `{sets: [{name, title, number, sheets}]}` |
| `GET /projects/{name}/drawing-sets/{set}.pdf` | the whole set as one PDF, one sheet per page in order |

All of these return 409 when the build is older than `design.py`. A drawing that
cannot be laid out (for example a diameter on a circle seen edge-on) is kept
with its `error`; its PDF/JSON return 422 with the message, and the rest of the
build is unaffected. A set containing such a sheet returns that sheet's 422 for
its PDF. The build stores the sets under `drawing_sets` beside `drawings`, and
each sheet's document carries its `set` (`name`, `title`, `number`, `index`,
`count`).

## Where the code is

* `tertius/drawing.py`: author API (`Drawing`, `DrawingSet`) and
  `project_drawing()` (exact HLR per view on the drawing's own part,
  placement, dimension resolution, set numbering).
* `server/core/compile_sandbox.py` (`timus_views`): projects declared drawings.
* `server/workflows/timus/part_drawing_pdf.py`: PDF rendering (ISO 5457
  border and zones, ISO 129 style dimensions, projection symbol, title block);
  `render_drawing_set_pdf()` for multi-page sets.
* `server/workflows/timus/timus_server.py`: the `drawings` and `drawing-sets`
  endpoints.
* `server/tests/test_part_drawings.py`.

## Not yet

Section views and hatching, detail views, hole tables, dimensions recorded
while modelling (imported into
views automatically), and showing the sheets in the drafting workbench UI.
