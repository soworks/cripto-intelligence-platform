# Crypto Intelligence Platform (cip)

AWS-native research and decision platform that scans the Binance Spot universe,
filters deterministically, invokes an LLM only for high-value analysis, and keeps
execution behind deterministic policy validation and human approval.

**Operating mode: SHADOW.** No real orders are placed.

- Architecture: `docs/architecture/reference-architecture-v1.0.md`
- Gap analysis: `docs/reviews/2026-10-03-gap-analysis.md`
- Roadmap: `docs/plans/2026-10-03-roadmap.md`
- ADRs: `docs/adr/`

## Develop

    make install   # uv sync + pre-commit
    make check     # ruff, mypy, pytest
    make build     # Lambda artifact in build/cip-lambda.zip
