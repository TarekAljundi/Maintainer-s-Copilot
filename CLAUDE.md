# Maintainer's Copilot

AIE Week 7 solo project: a self-hostable assistant that classifies, summarizes, retrieves, answers, and remembers — over pandas's issue corpus (swapped from fastapi for better class balance + ~10× more labeled records; see `DECISIONS.md` §Dataset). See `PRD.md` for the locked design.

## Agent skills

### Issue tracker

Issues live as markdown files under `.scratch/<feature>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Canonical five-role vocabulary, no overrides. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.
