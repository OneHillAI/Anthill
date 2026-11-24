# Spec: a repeatable evaluation of answers from the knowledge base

Status: implemented as a harness, a starter set and a stored baseline; the independent documents are not yet there (see Known limits). Lane: `pillar:knowledge`. Build plan step 2.0.

## Problem

Changes to how Anthill builds a prompt, remembers and removes things can make answers better or worse, and today nothing says which.
The speed benchmark (`tools/bench`) measures how fast a prompt is read, not whether the answer is right; the bakery questions used
so far were run by hand and are not kept. Without a repeatable check, a change to retrieval or to the prompt can only be judged by
reading a few answers.

## Requirements

1. **Cases are data.** A case is a small wiki, a question, and what a correct answer must say and must not say, or that the right
   answer is "it is not in the knowledge base". The kinds are lookup, update, correction, conflict, deletion, unanswerable and
   multi-source. A set is a `.jsonl` file under `tools/bench/knowledge_eval/sets/`; a malformed case, a duplicate id or an unknown
   kind is refused with a reason.
2. **Scoring uses no model.** An answer passes when every required group has one of its alternatives, no forbidden phrase appears, and
   an answer that should abstain says it does not know. Dashes and spaces models like to use are made plain first.
3. **A run uses the real answer path.** Each case gets a fresh wiki; a page marked removed is written and removed through the app's
   own code; the question goes through `ask_stream` as the chat does, with the answer cache off. The first word is timed.
4. **The result says what it was run on**: the set and its hash, how many cases were written by someone other than the builders, the
   Anthill commit, the engine version, each model's tag and digest, the scoring version, and per model the pass rate by kind, false
   abstentions, errors, and the first-word and total times. A model that is not installed stops the run before anything is asked;
   nothing is downloaded.
5. **Two results can be compared**: pass rate by kind and the median first word per model, with a note when the sets or the scoring differ.

## Known limits

- The starter set (14 cases) was written by the builders and is marked so. Step 2.0 asks for documents and questions the builders did
  not write; those need a source from outside, and the result file reports 0 independent cases until they are added.
- Scoring is by phrase, which is blunt: a correct answer in other words can fail, and a wrong answer that happens to contain a required
  phrase can pass. It is the same for every run, which is what a baseline needs.
- The baseline is one run on one Mac and one engine (Ollama 0.40.7), with the two models installed there; there is no 8B model in it. It shows today's behaviour, not a target.
- Fourteen cases are enough to see a large change, not a small one.
- This is a starter baseline, not the evaluation: a set of fourteen cases written by the builders is too small, and too close to a perfect
  score, to decide whether a change is acceptable. It cannot be the gate for a later step until it holds cases written by someone else.

## Baseline

Stored in `benchmarks/knowledge-eval/starter-20261123T225331Z.json` (the answers are kept, so it can be scored again with
`run.py --rescore FILE` without running a model; the file is at scoring version 2): `qwen2.5:3b` passed 12 of 14 and `qwen3.5:4b` 13
of 14, on the builders' starter set. Lookups, updates, corrections, conflicts and multi-source questions all passed on both. The
failures are about saying it is not known: `qwen2.5:3b` answered the removed door code and the unknown chief executive with a request
for more context and not an abstention, and `qwen3.5:4b` invented a number for a removed salary band (it did not give the removed
number). The median first word was 253 ms and 379 ms. The first scoring (version 1) passed 10 and 12; two phrasings of "it does not
say" were missed and the scorer was widened, which is why the file carries version 2.

## Acceptance

`tests/test_knowledge_eval.py`: the starter set is well formed, covers every kind and says it is the builders'; badly formed cases,
a duplicate id and a missing set are refused with a reason; scoring of required groups, forbidden phrases, abstentions and dashes;
a run through the real answer path with scripted answers stores a comparable result and never gives a removed page to the model; a
model that is not installed stops the run; the comparison names what got worse and warns when the sets differ.

## Not verified here

A model larger than 4B (none is installed), a run on another machine or engine, and a set written by anyone but the builders.
