# ADR 003: KnowledgeProvider abstraction over WeKnora

## Status
Accepted for Phase 1.

## Context
The final architecture treats WeKnora as an independent knowledge service. Phase 1 must not couple the echo runtime to WeKnora internals.

## Decision
Define a `KnowledgeProvider` Protocol with `search`, `get_chunks`, `ingest`, and `get_ingestion_status`. `WeKnoraKnowledgeProvider` is a client skeleton only; real retrieval is out of Phase 1 scope.

## Consequences
- Later phases can implement hybrid retrieval against WeKnora without changing graph contracts.
- Tests can use fake providers and must not call real model or knowledge services.
