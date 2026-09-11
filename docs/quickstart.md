# Quickstart

## Install

```bash
pip install agent-artifact-kit[s3]        # local + S3 destinations
pip install agent-artifact-kit[all]       # + Strands and MCP adapters
```

Regardless of which extras you install, the code always imports as `artifactkit`:

## Generate a structured document

```python
from artifactkit import (
    ArtifactService, ArtifactFormat, DocumentSpec, Heading, Paragraph,
    destination_from_uri,
)

spec = DocumentSpec(
    title="Q3 Summary",
    blocks=(
        Heading("Overview", level=1),
        Paragraph.of("Revenue grew 12% quarter over quarter."),
    ),
    base_filename="q3_summary",  # optional — bare name, no extension
)

service = ArtifactService()
result = service.create(
    spec,
    ArtifactFormat.DOCX,
    destination=destination_from_uri("s3://reports-bucket/q3"),
    include_presigned_url=True,
)
print(result.location, result.presigned_url)
```

Swap `ArtifactFormat.DOCX` for `PDF`, `PPTX`, or `XLSX` and pass the
matching spec type ([`PresentationSpec`][artifactkit.PresentationSpec] for pptx,
[`WorkbookSpec`][artifactkit.WorkbookSpec] for xlsx) — everything else about the call
stays the same.

## Styled presentations

Bare `python-pptx` output looks exactly like what it is: unstyled. Set
`theme` on `PresentationSpec` for a named preset (background, title/body
font and color, and an accent bar) applied automatically:

```python
from artifactkit import PresentationSpec, Theme, Slide

spec = PresentationSpec(
    title="Q3 Review",
    slides=(Slide(layout="title", placeholders={"title": "Q3 Review"}),),
    theme=Theme.VIBRANT,  # or CORPORATE, MINIMAL
)
```

For anything beyond these three presets, i.e. your own brand colors and
fonts, design a `.pptx` once and pass it as `template_path` instead;
`theme` and `template_path` are independent, you can use either, both,
or neither.

Themes differentiate two kinds of slides, the way a human-designed
deck does: slides using the `"title"` or `"section_header"` layout get
a bold gradient background, light title text, and a decorative corner
shape — the opening/section treatment. Every other layout gets a
clean solid background, a colored title, and a thin accent bar — the
readable treatment for slides people actually have to read.

The same three presets apply to `DocumentSpec` and `WorkbookSpec` too:

```python
from artifactkit import DocumentSpec, Heading, Paragraph, Theme

DocumentSpec(
    title="Q3 Summary",
    blocks=(Heading("Overview", level=1), Paragraph.of("...")),
    theme=Theme.CORPORATE,  # colors the title/headings, adds an accent
                            # rule under the title, styles table headers
)
```

```python
from artifactkit import WorkbookSpec, Sheet, Theme

WorkbookSpec(
    sheets=(Sheet(name="Data", header=("Item", "Qty"), rows=(("Widget", 3),)),),
    theme=Theme.MINIMAL,  # header fill, sheet tab color, frozen header
                          # row, and a native Excel Table with banded rows
)
```

Each backend interprets the same three names in whatever way fits its
format — a workbook doesn't have a "gradient background" concept the
way a slide does, so `XlsxBackend` reaches for what Excel actually
offers instead: a native `Table` object with one of Excel's own
built-in banded styles, a colored tab, and a frozen header row. A
workbook theming only applies to sheets that have a header — there's
no meaningful header row or table range to style otherwise, so a
headerless sheet with a theme set just renders unstyled rather than
raising.

## Conditional formatting

`Sheet.conditional_formats` takes a tuple of rules, independent of
`theme` — use either, both, or neither. Three rule types, each
mapping to a real Excel conditional formatting feature rather than
manually coloring cells:

```python
from artifactkit import WorkbookSpec, Sheet, ColorScaleRule, CellValueRule, DataBarRule

WorkbookSpec(sheets=(
    Sheet(
        name="Scores",
        header=("Name", "Score"),
        rows=(("Alice", 92), ("Bob", 54), ("Carol", 78)),
        conditional_formats=(
            # Red -> yellow -> green heatmap over the score column
            ColorScaleRule(cell_range="B2:B4", colors=("F8696B", "FFEB84", "63BE7B")),
            # Highlight scores above 80 with a green fill and bold dark-green text
            CellValueRule(
                cell_range="B2:B4", operator="greaterThan", values=("80",),
                fill_hex="C6EFCE", font_hex="006100", bold=True,
            ),
            # In-cell bar proportional to the score
            DataBarRule(cell_range="B2:B4", color_hex="638EC6"),
        ),
    ),
))
```

`ColorScaleRule.colors` takes 2 or 3 hex colors (low → high, or
low → mid → high). `CellValueRule.operator` is one of `greaterThan`,
`lessThan`, `equal`, `notEqual`, `greaterThanOrEqual`,
`lessThanOrEqual`, or `between` (which needs exactly 2 values instead
of 1) — validated at construction, so a bad operator or wrong value
count fails immediately rather than at render time.

## Deliver arbitrary files

```python
from artifactkit import ArtifactService, RawFile, FileBundleSpec, destination_from_uri

bundle = FileBundleSpec.from_directory("/tmp/agent_run", bundle_filename="run_output")
service = ArtifactService()
result = service.create_files(bundle, destination_from_uri("s3://reports-bucket/runs"))
# 5 or fewer files in the directory -> delivered individually
# 6+ -> bundled into run_output.zip
```

See [Architecture](architecture.md) for why these are two different
methods instead of one, and the [API reference](api-reference.md) for
every field on `RawFile`, `FileBundleSpec`, and the result types.

## Using it from an agent

Via Strands:

```python
from strands import Agent
from artifactkit.adapters.strands_tools import create_docx, create_pptx, create_xlsx, create_pdf, deliver_files

agent = Agent(tools=[create_docx, create_pptx, create_xlsx, create_pdf, deliver_files])
```

Via MCP:

```bash
python -m artifactkit.adapters.mcp_server
```

Both adapters call the exact same `ArtifactService` underneath — there
is no separate code path or separate correctness guarantee for
agent-driven calls versus calling `ArtifactService` directly in Python.

## Strands hooks

Three optional hook providers connect artifactkit to a Strands agent's
lifecycle (ships under the `[strands]` extra, `artifactkit.adapters.strands_hooks`).
None of them are required for the tools above to work — use whichever
solve a problem you actually have.

**Policy enforcement** — force a destination prefix and/or a theme on
every artifactkit tool call an agent makes, regardless of what the
agent asked for:

```python
from strands import Agent
from artifactkit.adapters.strands_tools import create_docx, create_pptx
from artifactkit.adapters.strands_hooks import ArtifactPolicyHookProvider

agent = Agent(
    tools=[create_docx, create_pptx],
    hooks=[ArtifactPolicyHookProvider(
        destination_prefix="s3://approved-bucket/reports",
        required_theme="corporate",
    )],
)
```

**Tracing bridge** — correlate Strands' own invocation with
artifactkit's `operation_id` in the logs, instead of the two systems
being stitched together by timestamp guessing:

```python
from artifactkit.adapters.strands_hooks import ArtifactTracingHookProvider

agent = Agent(tools=[...], hooks=[ArtifactTracingHookProvider()])
```

**Auto-delivery** — after a code-execution tool call finishes, deliver
whatever it wrote to an `outputs/` subdirectory automatically, instead
of relying on the agent to remember to call `deliver_files` itself:

```python
from artifactkit import ArtifactService, destination_from_uri
from artifactkit.adapters.strands_hooks import ArtifactAutoDeliveryHookProvider

hook = ArtifactAutoDeliveryHookProvider(
    service=ArtifactService(),
    destination=destination_from_uri("s3://reports-bucket/runs"),
    code_tool_names=frozenset({"execute_code"}),  # whatever your code tool is named
    output_dir_extractor=lambda result: result["session_dir"],  # tool-specific — see note below
)
agent = Agent(tools=[..., code_tool], hooks=[hook])
```

`output_dir_extractor` has no default. Where a code-execution tool
writes its output is specific to that tool (AgentCore Code
Interpreter, a subprocess shell tool, something else), and this
provider can't guess correctly — check your tool's actual result
shape before wiring this up, don't assume the sketch above matches it.

## Custom PowerPoint templates

`PresentationSpec.template_path` renders against a real branded
`.pptx` instead of python-pptx's bare default — but guessing at a
custom template's layout names and placeholder structure is
unreliable. Inspect it first:

```python
from artifactkit import inspect_template

info = inspect_template("/path/to/brand_template.pptx")
for layout in info.layouts:
    print(layout.index, layout.name)
    for ph in layout.placeholders:
        print(f"  idx={ph.idx} name={ph.name!r} type={ph.type}")
```

Then target placeholders precisely instead of hoping positional order
lines up — useful the moment a layout has more than one content
placeholder (a "Two Content" comparison layout, for instance, where
positional fill has no principled way to know which text goes left
and which goes right):

```python
from artifactkit import PresentationSpec, Slide

spec = PresentationSpec(
    title="Comparison",
    slides=(
        Slide(
            layout="Two Content",
            placeholders={
                "title": "Before vs After",
                "idx:1": "Before: manual process, 3 days",       # by placeholder idx
                "Content Placeholder 3": "After: automated, 2 hours",  # or by exact name
            },
        ),
    ),
    template_path="/path/to/brand_template.pptx",
)
```

`"title"` still works exactly as before for the common case. Anything
that isn't `"title"`, an `"idx:N"` key, or an exact placeholder name
falls back to filling remaining placeholders in order — so existing
specs that only ever used `{"title": ..., "body": ...}` keep working
unchanged.

**`theme` and `template_path` don't combine.** A custom template
already carries its own intentional design; forcing a gradient/accent
theme on top of it would fight that design rather than respect it. If
both are set, `theme` is skipped (logged as a warning, not silent) and
the template's own look is what renders. Via Strands or MCP, this
workflow is `inspect_pptx_template` followed by `create_pptx`.

## A note on what this library does not do

artifactkit never executes code and never generates content (no LLM
calls, no image generation). It delivers bytes that already exist. If
a workflow step needs to run code and produce output files, that
execution happens entirely outside this library — artifactkit picks
up once the output exists, via `RawFile(source_path=...)` or
[`FileBundleSpec.from_directory()`][artifactkit.FileBundleSpec.from_directory].
