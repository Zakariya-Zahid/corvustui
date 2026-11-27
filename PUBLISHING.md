# Publishing CorvusTUI to PyPI

The package is ready for PyPI: metadata, MIT license, entry point, TUI
stylesheet, and README are included. The `corvustui` name was available on
PyPI when this guide was written.

## 1. Prerequisites

- A [PyPI account](https://pypi.org/account/register/) with 2FA enabled.
- An API token from <https://pypi.org/manage/account/token/>.
  - For the first upload, create an account-scoped token.
  - After the project exists, switch to a project-scoped token.
- `uv` installed (`curl -LsSf https://astral.sh/uv/install.sh | sh`).

Never commit a token. Pass it through the environment or the `--token` flag.

## 2. Prepare the release

1. Bump the version in `pyproject.toml` (PyPI never allows re-uploading the
   same version):

   ```toml
   version = "0.1.1"
   ```

2. Run the release gates from the repository root:

   ```bash
   python -m pytest -q
   ruff check src tests --select F,E9,I001
   ./bend/bin/bend PROOF.bend
   ```

3. Build clean distributions:

   ```bash
   rm -rf dist build src/*.egg-info
   uv build
   ls -lh dist/
   ```

   You should see a wheel and an sdist:

   ```text
   corvustui-0.1.1-py3-none-any.whl
   corvustui-0.1.1.tar.gz
   ```

4. Validate the metadata and package contents:

   ```bash
   uvx twine check dist/*
   # PASSED for both files

   unzip -l dist/corvustui-*.whl | grep -E 'app\.tcss|LICENSE|entry_points'
   # corvustui/tui/styles/app.tcss
   # corvustui-.../licenses/LICENSE
   # corvustui-.../entry_points.txt
   ```

5. Optional local install test from the built wheel:

   ```bash
   uv tool install --force dist/corvustui-*.whl
   corvustui --help
   ```

## 3. Dry run

```bash
uv publish --dry-run dist/*
```

This checks the upload without sending files.

## 4. TestPyPI (recommended first)

Create a separate token at <https://test.pypi.org/manage/account/token/>.

```bash
uv publish \
  --publish-url https://test.pypi.org/legacy/ \
  --token pypi-YOUR_TEST_TOKEN \
  dist/*
```

Install from TestPyPI (dependencies come from real PyPI):

```bash
uv tool install \
  --index-url https://test.pypi.org/simple/ \
  --extra-index-url https://pypi.org/simple/ \
  --index-strategy unsafe-best-match \
  "corvustui[all]"
```

If it works, uninstall it before testing the real release:

```bash
uv tool uninstall corvustui
```

## 5. Publish to PyPI

```bash
UV_PUBLISH_TOKEN=pypi-YOUR_TOKEN uv publish dist/*
```

Or pass the token directly:

```bash
uv publish --token pypi-YOUR_TOKEN dist/*
```

Verify:

- Project page: <https://pypi.org/project/corvustui/>
- Install:

  ```bash
  uv tool install "corvustui[all]"
  # or
  pipx install "corvustui[all]"
  ```

## 6. Tag the GitHub release

```bash
git tag -a v0.1.0 -m "CorvusTUI v0.1.0"
git push origin v0.1.0
```

Use the same version as `pyproject.toml`.

## 7. Trusted publishing from GitHub Actions (optional)

Trusted publishing removes long-lived API tokens. Configure it once on PyPI:

1. Open <https://pypi.org/manage/account/publishing/>.
2. Add a pending publisher:
   - PyPI project name: `corvustui`
   - Owner: `Zakariya-Zahid`
   - Repository: `corvustui`
   - Workflow name: `publish.yml`
   - Environment: `pypi`
3. Add `.github/workflows/publish.yml`:

   ```yaml
   name: Publish to PyPI

   on:
     release:
       types: [published]

   jobs:
     pypi:
       runs-on: ubuntu-latest
       environment: pypi
       permissions:
         id-token: write
         contents: read
       steps:
         - uses: actions/checkout@v4
         - uses: astral-sh/setup-uv@v5
         - run: uv build
         - uses: pypa/gh-action-pypi-publish@release/v1
   ```

4. Publish a GitHub release. The workflow builds and uploads the package.

## Version rules

- `0.1.0` -> `0.1.1` for fixes.
- `0.1.0` -> `0.2.0` for new features.
- `0.1.0` -> `1.0.0` for a stable public API.

A version can be yanked, but not replaced. Bump before every upload.
