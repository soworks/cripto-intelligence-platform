# ADR-0001: Deterministic risk authority; LLM recommends only

Status: Accepted (2026-10-03)

## Context
LLM outputs are probabilistic and can be wrong, manipulated by injected text, or
schema-valid but unsafe.

## Decision
Code computes features, filters, scores, sizes, and validates. The LLM returns a
schema-constrained recommendation that the deterministic policy validator may
reject. No LLM has tools that can place orders. The validator never reads LLM
free text as an instruction.

## Consequences
Every proposal is reproducible from stored inputs, policy version, and prompt
version. LLM value is measured against a deterministic-only baseline (ablation).
