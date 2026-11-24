# Knowledge evaluation

How well does Anthill answer from its knowledge base, and how fast does the first word arrive? One set of cases, scored without a
model, run through the real answer path (`ask_stream`) with a local model, stored as a result file that two runs can be compared on.

A **case** (`sets/*.jsonl`, one JSON object per line) is a small wiki (`wiki`: slug to page text), a `question`, and what a correct
answer says: `must_include` (a list of groups; each group needs one of its alternatives), `must_not_include`, `abstain` (the right
answer is "it is not in the knowledge base") and `removed` (pages written and then removed before the question). `kind` is one of
`lookup`, `update`, `correction`, `conflict`, `deletion`, `unanswerable`, `multi_source`. `independent` says whether someone other
than the builders wrote the documents and the question; a result file records how many cases were independent, so a baseline made
from the builders' own cases is never mistaken for an independent one.

```bash
python tools/bench/knowledge_eval/run.py --set starter --model qwen2.5:3b --model qwen3:8b
python tools/bench/knowledge_eval/compare.py before.json after.json
```

It needs a local Ollama with the models already installed (it never pulls one) and the embedding model Anthill uses for retrieval.
The answer cache is off for the run. Results go to `benchmarks/knowledge-eval/`.

Scoring is by phrase: a required fact is present, a forbidden one is absent, and an answer that should abstain says it does not know.
That is blunt (a right answer in other words can fail) and it cannot be flattered by the model that wrote the answer. A set grows by
adding lines; change `SCORING_VERSION` in `score.py` when the scoring changes so old and new files are not compared silently.
