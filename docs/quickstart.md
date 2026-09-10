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

## A note on what this library does not do

artifactkit never executes code and never generates content (no LLM
calls, no image generation). It delivers bytes that already exist. If
a workflow step needs to run code and produce output files, that
execution happens entirely outside this library — artifactkit picks
up once the output exists, via `RawFile(source_path=...)` or
[`FileBundleSpec.from_directory()`][artifactkit.FileBundleSpec.from_directory].
