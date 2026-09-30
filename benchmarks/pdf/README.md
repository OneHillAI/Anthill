# PDF extraction benchmark

This deterministic synthetic corpus covers text-heavy, scanned, table/form, multi-column,
mixed text/diagram, and long PDFs. Regenerate it with:

```bash
python scripts/generate_pdf_benchmark_fixtures.py
```

Compare Anthill's former `pypdf` text extraction with the local stream-only MarkItDown path.
Pass an Ollama model to also ask one fixed downstream question per fixture:

```bash
python scripts/benchmark_pdf_extraction.py
python scripts/benchmark_pdf_extraction.py --ollama-model qwen2.5:3b
python scripts/benchmark_pdf_extraction.py --ollama-model qwen2.5:3b --anthill-vision-e2e
```

## Baseline result

Run on 2026-07-16 with Python 3.13, MarkItDown 0.1.6, and local `qwen2.5:3b`. Recall is
the fraction of fixture sentinels present in extracted text; answer recall is the fraction of expected
answer facts returned by the model. Timings and Python allocation peaks are advisory and vary by
machine.

| Fixture | Converter | Recall | Markdown table | Characters | Latency ms | Peak KiB | Answer recall |
|---|---|---:|:---:|---:|---:|---:|---:|
| text-heavy.pdf | pypdf | 1.00 | n/a | 6103 | 28.2 | 221.3 | 1.00 |
| text-heavy.pdf | markitdown | 1.00 | n/a | 6194 | 533.6 | 4292.2 | 1.00 |
| scanned.pdf | pypdf | 0.00 | n/a | 0 | 3.0 | 80.9 | 0.00 |
| scanned.pdf | markitdown | 0.00 | n/a | 0 | 25.6 | 263.4 | 0.00 |
| table-form.pdf | pypdf | 1.00 | false | 102 | 4.1 | 77.0 | 1.00 |
| table-form.pdf | markitdown | 1.00 | true | 199 | 28.4 | 351.2 | 1.00 |
| multi-column.pdf | pypdf | 1.00 | n/a | 185 | 4.3 | 94.9 | 1.00 |
| multi-column.pdf | markitdown | 1.00 | n/a | 192 | 36.4 | 504.4 | 1.00 |
| long.pdf | pypdf | 1.00 | n/a | 27294 | 165.2 | 657.4 | 1.00 |
| long.pdf | markitdown | 1.00 | n/a | 27955 | 2410.1 | 3104.7 | 1.00 |

MarkItDown retained all text and downstream answer facts that pypdf retained and materially improved
the structured fixture by producing a Markdown table. It costs more latency and memory. Neither text
converter performs OCR. Anthill's separate local vision fallback was therefore exercised end-to-end
with installed `qwen3.5:9b`; it recovered both `PO-8841` and `4,250 EUR` from `scanned.pdf` without
sending the document outside Ollama.

The mixed-diagram fixture now embeds the Queue to Worker to Archive relationship only in a raster
image; the earlier mixed rows were removed because their full answer was duplicated as extractable
prose. Text-only downstream answer recall is therefore expected to be zero for that relationship.
The `--anthill-vision-e2e` mode requires loopback Ollama, runs the committed fixture through Anthill's
real combined structured-text and installed local-vision path, and exits with an error unless the final
page recovers all three visual-only components. Unit tests keep the model deterministic; this explicit
benchmark mode supplies the real local-model evidence.
