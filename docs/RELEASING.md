# Release checklist

1. Update `version.py`, `pyproject.toml`, and the dated version section in `CHANGELOG.md`. Run `uv lock` to synchronize package metadata.
2. Run the complete Python tests, frontend tests, syntax checks, and `git diff --check`. Do not commit runtime data or credentials.
3. Push the reviewed commit to `main` and wait for both CI Python versions to pass before tagging it.
4. Generate English notes for exactly the version being released:

   ```powershell
   python scripts/release_notes.py 2.8.0 --output "$env:TEMP/novel-v2.8.0-notes.md"
   ```

5. Read the generated file. Create/push the corresponding tag, then use `gh release create v2.8.0 --verify-tag --title "v2.8.0 - Style Preset Workbench" --notes-file "$env:TEMP/novel-v2.8.0-notes.md"` (substitute the current version and title). Never pass the complete changelog as release notes.
6. Verify the remote release body, package assets, checksums, and container workflow. PyPI publication remains a separate explicitly enabled workflow.

If a published version needs a correction, prefer a new patch release. Do not move published version tags or overwrite an existing release's installation assets.
