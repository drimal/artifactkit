# Publishing artifactkit to PyPI

## First-time setup

1. Confirm the name is still free: https://pypi.org/project/agent-artifact-kit/
   (a text search can miss a reserved-but-empty name -- check the URL directly)
2. Create a PyPI account and, separately, a TestPyPI account: https://pypi.org/account/register/
3. Fix the placeholder URLs in `pyproject.toml` (`[project.urls]`) once the
   repo has a real home -- PyPI displays these on the project page.
4. Recommended: use Trusted Publishing (OIDC from GitHub Actions) instead of
   a long-lived API token: https://docs.pypi.org/trusted-publishers/
   If publishing manually instead, generate a scoped API token under
   PyPI account settings and use `__token__` as the username when prompted.

Note the name split: the PyPI/distribution name is
`agent-artifact-kit` (set in `pyproject.toml`'s `[project] name`),
but the importable package stays `artifactkit` (`[tool.hatch.build.targets.wheel]`
still points at the `artifactkit/` directory). Don't rename one without
checking the other -- `pip install agent-artifact-kit` should
always leave you with `import artifactkit` working, and the `all` extra's
self-reference (`agent-artifact-kit[s3,strands,mcp]`) must use
the distribution name, not the import name, or extras resolution breaks.

## Documentation

```bash
pip install -e ".[docs]"
mkdocs serve          # live preview at http://127.0.0.1:8000
mkdocs build --strict # what CI should run before merging docs changes
```

`--strict` turns warnings (broken `::` autodoc references, bad internal
links) into build failures — always run it before publishing, not just
`mkdocs build`.

## Every release

```bash
# from the package root
pip install --upgrade build twine

# 1. bump version in pyproject.toml, then:
rm -rf dist/
python -m build
twine check dist/*          # catches metadata/README rendering problems
                             # before they reach PyPI

# 2. dry run against TestPyPI first
twine upload --repository testpypi dist/*
pip install --index-url https://test.pypi.org/simple/ agent-artifact-kit

# 3. the real upload
twine upload dist/*
```

## Before the first real upload

- Run the test suite: `pytest tests/ -v` (87 tests with all extras
  installed; 78 pass + 9 skip cleanly without `[strands]`/`[mcp]`)
- Run `pytest --cov=artifactkit` if you want a coverage number to publish
- Confirm `pip install agent-artifact-kit[all]` in a fresh venv pulls everything
  cleanly (this is what a new user's first experience will be)
- Double check `LICENSE` and the `authors` field in `pyproject.toml` reflect
  who should actually be listed

## Versioning

`pyproject.toml` version is the single source of truth. There is no
dynamic versioning configured, so bump it by hand before each release
and tag the commit to match (`git tag v0.1.1`).
