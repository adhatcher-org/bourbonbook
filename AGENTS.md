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

## PR review, validation, and approval

Before opening or updating a pull request for implementation changes:

1. Complete the implementation and focused tests in the primary session.
2. Use `bourbonbook_reviewer` for preliminary review while iterating, and resolve every actionable
   finding in the primary session.
3. Create the final candidate commit in the primary session.
4. Spawn `bourbonbook_reviewer` against that exact commit. A final `PASS` must include
   `reviewed_commit: <sha>` matching the candidate commit, with no intended scope left uncommitted.
5. Spawn `pr_validator` in local validation mode against the same commit. It must run
   `make pr-review` and a final `PASS` must include `validated_commit: <sha>` matching the candidate.
6. Treat reviewer or validator `FAIL` findings as actionable work for the primary session. Any fix
   requires a new candidate commit and fresh commit-bound runs of both agents.
7. Open or update the pull request as a draft only after both final agents return `PASS` for the
   same candidate commit. Include both verdicts, commit SHAs, and `make pr-review` in the description.
8. After that commit is pushed, invoke `pr_validator` again in remote approval mode with the
   repository, pull-request number, expected head SHA, and both commit-bound verdicts. It must wait
   for the complete expected GitHub check set and bind any approval to that exact head commit.
9. The remote validator may approve only when every requirement passes and the authenticated
   GitHub identity is allowed to approve. It must return `BLOCKED` rather than claim success when
   GitHub forbids self-approval or the credential lacks review permission.
10. Approval is not authorization to merge the pull request.

Preserve unrelated user changes throughout validation. Never stage files merely because test or
build tooling generated them.
