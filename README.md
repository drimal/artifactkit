# artifactkit

Installs as `agent-artifact-kit`, imports as `artifactkit`
(same split as `beautifulsoup4` → `bs4`: the PyPI name is descriptive
for discoverability, the import name is short because that's what you
actually type in code).

Generic artifact creation harness for agentic workflows. Agents build a
declarative content spec; artifactkit renders it to docx/pptx/xlsx/pdf,
writes it to a destination (local disk or S3), verifies the write, and
returns the location. No agent workflow reimplements this on its own.

## Install

```
pip install agent-artifact-kit[s3]        # local + S3
pip install agent-artifact-kit[all]       # + Strands and MCP adapters
```

All code below imports the package as `artifactkit` regardless of
which extras you installed.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
pytest tests/ --cov=artifactkit --cov-report=term-missing   # 93% with all extras installed (131 tests)
```

See `PUBLISHING.md` for how to build and release to PyPI, and
`mkdocs.yml` / `docs/` for the full documentation site (`pip install
-e ".[docs]"` then `mkdocs serve`).

## Strands hooks

Beyond the tool functions, `artifactkit.adapters.strands_hooks` (under
the `[strands]` extra) provides three optional hook providers for a
Strands agent's lifecycle: policy enforcement (force a destination/theme
on every artifactkit call), a tracing bridge (correlate Strands'
invocation with artifactkit's `operation_id` in the logs), and
auto-delivery (deliver whatever a code-execution tool wrote, without
the agent having to remember to call `deliver_files` itself). See
`docs/quickstart.md` for usage — all three were verified against the
real `strands-agents` API (event mutability, shared invocation state,
result serialization), not assumed from documentation.

## Observability

Every call is logged (structured, correlated by an `operation_id`
returned on the result) and, if you supply a `MetricsSink`, measured.
See `docs/observability.md` for the exact log events, fields, and
metric names, or wire in your own sink:

```python
from artifactkit import ArtifactService, MetricsSink

class MySink:
    def increment(self, name, tags=None): ...
    def timing(self, name, duration_ms, tags=None): ...

service = ArtifactService(metrics=MySink())
```

## Plain Python

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
    # filename omitted: resolves to "q3_summary.docx" from spec.base_filename,
    # adapted to whatever format is requested. Pass filename= explicitly to
    # override it for this call without changing the spec.
)
print(result.location, result.presigned_url)
```

## Strands agent

```python
from strands import Agent
from artifactkit.adapters.strands_tools import create_docx, create_pptx, create_xlsx, create_pdf

agent = Agent(tools=[create_docx, create_pptx, create_xlsx, create_pdf])
```

## MCP server

```
python -m artifactkit.adapters.mcp_server
```

Any MCP-capable agent can then call `create_docx` / `create_pptx` /
`create_xlsx` / `create_pdf` with a JSON spec, a `destination_uri`, and
a `filename`.

## Spec shape

See `artifactkit/core/parsing.py` for the exact dict shape each tool
expects — it's the single source of truth both adapters parse against.

## Styling

Bare output from python-docx/pptx/openpyxl looks exactly like what it
is: unstyled. Set `theme` on any structured spec for a named preset
(`Theme.VIBRANT`, `Theme.CORPORATE`, `Theme.MINIMAL`) that each backend
applies in whatever way fits its format — colored headings and an
accent rule in a doc, a gradient hero slide with a decorative shape in
a deck, a native Excel Table with banded rows and a colored tab in a
workbook. See `docs/quickstart.md` for examples of all three.

For a real branded `.pptx`, use `template_path` instead — `inspect_template()`
reads a template's layouts and placeholders first so you can target
them precisely (`"idx:1"` or an exact placeholder name), rather than
guessing at what a custom template contains. `theme` and `template_path`
don't combine: a template's own design wins.

`Sheet.conditional_formats` adds real Excel conditional formatting —
`ColorScaleRule` (heatmap), `CellValueRule` (highlight cells matching
a comparison), `DataBarRule` (in-cell proportional bars) — independent
of `theme`. See `docs/quickstart.md`.

## Arbitrary files (code, images, anything else)

`create()` is for structured content that needs rendering (docx/pptx/xlsx/pdf).
For content you already have, code files, images, data files, whatever,
use `create_files()` instead. It skips rendering entirely and goes
straight to write + verify. If you hand it more than 5 files, it
bundles them into a single `.zip` automatically rather than doing one
write per file.

Each `RawFile` takes its content one of two ways:

```python
from artifactkit import ArtifactService, RawFile, FileBundleSpec, destination_from_uri

# In-memory bytes (e.g. code the agent just wrote)
RawFile("main.py", content=b"print('hello')\n")

# A path already on disk (e.g. output from code that ran, a generated
# image, anything already written somewhere) -- read lazily at write
# time, never loaded into memory until it's actually needed.
RawFile("chart.png", source_path="/tmp/agent_run/chart.png")
```

If a piece of code produced a whole directory of output, skip
building `RawFile`s by hand:

```python
bundle = FileBundleSpec.from_directory("/tmp/agent_run", bundle_filename="run_output")
service = ArtifactService()
result = service.create_files(bundle, destination_from_uri("s3://reports-bucket/runs"))
# 5 or fewer files in the directory -> delivered individually
# 6+ -> bundled into run_output.zip
```

Via Strands or MCP, this is the `deliver_files` tool. Each file entry
needs exactly one of `content_base64` (for content the agent is
holding directly) or `source_path` (for content already on disk,
avoids hauling large payloads through the model's context just to
hand them back to this tool).

**artifactkit never executes code or generates content.** It only
delivers bytes that already exist. If `app.py` needs to run and
produce files, that execution happens entirely outside this library,
with whatever sandboxed runtime the agent already has, and
`create_files()` picks up only once the output exists on disk.

## Design notes

- Render, validate, and write/verify are three separate stages. Only
  the write/verify stage retries; render and validate failures are
  deterministic and retrying wastes time.
- `ArtifactValidationError` (bad spec/render) and `ArtifactWriteError`
  (write couldn't be verified) are distinct exception types so a
  calling agent can choose the right remediation.
- Destinations are pluggable via the `OutputDestination` protocol.
  Adding GCS/Azure support means one new class, no changes to backends
  or the service.
- `create()` and `create_files()` share the same write/verify/retry/
  presign machinery (`ArtifactService._write_verified`), the only
  difference is whether there's a render+validate step first. Arbitrary
  files skip it because there's no format-specific structure to check
  on content the caller already fully formed.
