# Bourbon Book Agent Instructions

## Project skills

Use the available skill that matches the work:

- Use `$roadmap-action` when implementing or completing an action from `plan.md`.
- Use `$migration-change` for model, schema, Alembic, migration-bootstrap, or persistent-data
  changes.
- Use `$pwa-visual-check` for templates, CSS, JavaScript, forms, responsive UI, uploads, icons,
  manifests, or service-worker behavior.
- Use `$provider-evaluation` for Ollama, OpenAI, pricing search, embeddings, Qdrant, prompts,
  structured outputs, fallbacks, or provider usage accounting.

Use multiple skills when a change crosses these boundaries. Follow each selected skill's workflow
in addition to the review and validation sequence below.

## Pre-PR review and validation

Before opening or updating a pull request for implementation changes:

1. Complete the implementation and focused tests in the primary session.
2. Spawn the project-scoped `bourbonbook_reviewer` agent in a separate subagent session. Give it the
   intended change scope and base branch, and ask it to independently review the complete diff.
3. Resolve every actionable reviewer finding in the primary session and ask
   `bourbonbook_reviewer` to review again until it returns `PASS`.
4. Spawn the project-scoped `pr_validator` agent in a separate subagent session with the same scope
   and base branch. The validator must run `make pr-review` and must not modify implementation files
   or Git state.
5. Treat validator `FAIL` findings as actionable work for the primary session. If a fix changes the
   implementation, repeat the reviewer pass before asking `pr_validator` to validate again.
6. Do not open or update the pull request until the latest runs of both agents return `PASS`.
7. Include both agent verdicts and the `make pr-review` result in the pull-request description.

Preserve unrelated user changes throughout validation. Never stage files merely because test or
build tooling generated them.
