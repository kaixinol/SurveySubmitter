---
name: conventional-commit
description: "Generate Git commit messages following the Conventional Commits 1.0.0 specification."
version: 1.0.0
author: QoderCN (migrated)
license: MIT
---

# Conventional Commit

Generate Git commit messages following the Conventional Commits 1.0.0 specification.

## Format

```text
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

- The subject line must be concise (preferably under 72 characters).
- Use the imperative mood.
- Do not end the subject with a period.
- Use lowercase types.
- Scopes are optional.
- Include a body only when additional context is useful.
- Include footers only when required (e.g. BREAKING CHANGE, Refs).

## Common types

- feat — new feature
- fix — bug fix
- docs — documentation
- style — formatting or style-only changes
- refactor — code restructuring without changing behavior
- perf — performance improvements
- test — tests
- build — build system or dependencies
- ci — CI/CD
- chore — maintenance tasks
- revert — revert a previous commit

## Custom types

Conventional Commits allows additional project-specific types.

Examples include:

- upgrade — dependency or runtime upgrades
- remove — removing obsolete code or functionality
- workflow — workflow-related changes
- ui — UI-specific changes
- python — Python runtime or tooling changes
- notes — comments or developer notes
- guard — validation or safety improvements
- skill — AI skill definitions or prompts

These custom types do not imply semantic version changes unless the commit is marked as a breaking change.

## Breaking changes

Breaking changes should use either:

```text
feat!: remove legacy API
```

or

```text
feat: remove legacy API

BREAKING CHANGE: legacy API has been removed.
```

## Examples

```text
feat(parser): support nested arrays

fix(api): prevent timeout on slow requests

docs: update README

chore(deps): upgrade axios to 1.12.0

upgrade: bump Python to 3.13.14

workflow: add release workflow

skill: add commit message generation skill

refactor(parser): simplify regex matching

perf(cache): reduce memory allocations

feat(api)!: remove deprecated endpoint

BREAKING CHANGE: /v1 endpoint has been removed.
```

## Output requirements

- Output only the commit message.
- Do not wrap the output in Markdown.
- Do not explain the commit message.
- Infer the most appropriate type from the provided changes.
- Prefer standard Conventional Commit types when they accurately describe the change.
- Use project-specific custom types only when they convey clearer intent.
