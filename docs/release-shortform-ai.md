# shortform-ai Release Runbook

This repository publishes the standalone `shortform-ai` CLI. The PyPI
wheel/sdist and optional Homebrew tap/formula are the public distribution
artifacts.

## Local Release Check

Run this before tagging or uploading anything:

```bash
make release-cli
```

The script runs CLI tests, scans release files and built artifacts for
real-looking secrets, builds the Python wheel/sdist, and syntax-checks the
Homebrew formula when Ruby is available.

Release artifacts are written to:

```text
dist/shortform-ai/
```

## Sensitive Information Gate

The release check rejects source or built artifacts that contain real-looking:

- OpenAI API keys
- GitHub classic or fine-grained tokens
- Google API keys
- Instagram `sessionid` cookies
- literal bearer tokens

It also runs `shortform-ai doctor` with a fake Instagram session and verifies
that the raw value is not printed. Test fixtures such as `sk-test` and
placeholder Homebrew SHA values are allowlisted.

You can run the secret gate by itself:

```bash
make check-release-cli
```

## PyPI

Recommended publishing method: PyPI Trusted Publishing from GitHub Actions.
This avoids storing a long-lived PyPI token in GitHub secrets.

1. Create or log into the PyPI account that should own `shortform-ai`.
2. Add a pending trusted publisher for the project:
   - PyPI project name: `shortform-ai`
   - Owner/repository: the public `shortform-ai` GitHub repo
   - Workflow name: `release-shortform-ai.yml`
   - Environment name: `pypi`
3. Add a GitHub environment named `pypi`.
4. Protect the `pypi` environment with a required manual reviewer.
5. Create an annotated tag like:

```bash
git tag -a v0.1.0 -m "shortform-ai v0.1.0"
git push origin v0.1.0
```

The GitHub release workflow should build distributions, run the release check,
and publish with `pypa/gh-action-pypi-publish`.

Official references:

- PyPI Trusted Publishing: https://docs.pypi.org/trusted-publishers/
- Publishing with a trusted publisher: https://docs.pypi.org/trusted-publishers/using-a-publisher/
- Python packaging guide for GitHub Actions publishing: https://packaging.python.org/en/latest/guides/publishing-package-distribution-releases-using-github-actions-ci-cd-workflows/

## Homebrew

Use a tap repository, for example:

```text
github.com/<owner>/homebrew-shortform-ai
```

Homebrew expects tap repos to be named `homebrew-<tap>`, and users install with:

```bash
brew tap <owner>/shortform-ai
brew install shortform-ai
```

For a public Homebrew install, point `Formula/shortform-ai.rb` at the public
PyPI sdist URL after the PyPI release, then replace all placeholder SHA256
values.

Typical formula finalization:

1. Publish `shortform-ai` to PyPI.
2. Download or copy the PyPI sdist URL for `shortform_ai-0.1.0.tar.gz`.
3. Compute the sdist SHA:

```bash
curl -L -o /tmp/shortform_ai-0.1.0.tar.gz "<pypi-sdist-url>"
shasum -a 256 /tmp/shortform_ai-0.1.0.tar.gz
```

4. Update `Formula/shortform-ai.rb` `url` and `sha256`.
5. Replace Python resource placeholder SHA values. Homebrew can help in a tap:

```bash
brew update-python-resources shortform-ai
```

6. Test the formula:

```bash
brew install --build-from-source ./Formula/shortform-ai.rb
brew test shortform-ai
```

Official references:

- Homebrew taps: https://docs.brew.sh/Taps
- Homebrew Python formula guidance: https://docs.brew.sh/Homebrew-and-Python

## Final Human Checklist

- `make release-cli` passes.
- `shortform-ai` package name is still available on PyPI immediately before publishing.
- PyPI trusted publisher is configured for the exact workflow/environment.
- Release artifacts do not include `.env`, Chrome cookies, Keychain values, or local output directories.
- Homebrew formula SHA placeholders are replaced before publishing the tap.
