# Spec: structured PDF ingestion

Status: implemented
Lane: `pillar:knowledge`
Relates to: [`docs/USING_ANTHILL.md`](../USING_ANTHILL.md), [`anthill/multimodal/reader.py`](../../anthill/multimodal/reader.py), and [`anthill/wiki/ingest.py`](../../anthill/wiki/ingest.py)

## Problem

PDF uploads can contain more than plain text. Anthill needs to recover useful document structure and visual facts without allowing an untrusted PDF to consume unbounded local resources or leave the source unavailable for later review.

## Requirements

- **R1 - Structured extraction:** PDF ingestion SHALL preserve readable text and recover supported layout, table, image, and vector facts when they are materially useful to the document.
- **R2 - Bounded work:** Ingestion SHALL enforce source, page, image, decoded-data, output, model-call, memory, and wall-clock limits before or during processing.
- **R3 - Local processing:** PDF parsing and optional visual extraction SHALL run locally through the bounded worker path; no PDF content SHALL be sent to a remote service by the ingestion pipeline.
- **R4 - Source preservation:** The validated raw PDF SHALL be copied into the workspace before inference, including when processing is rejected or needs confirmation, so users can retry or audit it.
- **R5 - Safe failure:** Deterministic safety and parse failures SHALL be rejected or quarantined with an actionable status. Transient worker and host failures SHALL remain retryable and SHALL not be reported as a file safety violation.
- **R6 - Explicit confirmation:** PDFs above automatic-work limits but below hard safety limits SHALL require an explicit confirmation before processing.

## Acceptance criteria

- Model-free tests cover structured extraction, hard limits, confirmation, source preservation, quarantine, retryable failures, and routing through the PDF path.
- The packaged-app self-check exercises PDF extraction with bundled dependencies.
- The CLI and web upload flows expose confirmation and distinguish permanent rejection from transient retryable failure.
- PDF benchmark fixtures and a repeatable extraction benchmark cover text-heavy, scanned, tabular, multi-column, mixed-visual, and long documents.
