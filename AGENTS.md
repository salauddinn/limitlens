# Agent Instructions

## Privacy and portability

- Do not include personal identifiers, user-specific machine details, absolute local paths, usernames, emails, tokens, cookies, or private environment values in committed files, docs, examples, logs, or final summaries.
- Prefer portable placeholders such as `<project-root>`, `<config-dir>`, `<cache-dir>`, `<user-home>`, or `~` when documenting paths.
- Treat local config, cache files, sessions, credentials, API keys, cookies, and command output as potentially sensitive.
- Redact or generalize any incidental local/system-specific data before sharing or committing.

## Repository hygiene

- Do not commit scratch scripts, debug dumps, temporary files, local caches, or generated artifacts unless they are intentionally part of the project.
- Before committing or reporting completion, check `git status --short` and ensure only intentional files are changed.
- Keep examples generic and reusable across environments.

## Validation

- Run focused tests for changed code when possible.
- For broad changes, run the full test suite before declaring the work complete.
- Report validation commands and results concisely.
