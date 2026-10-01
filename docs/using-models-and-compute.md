# Models & compute

## Choosing your model

Anthill lists the current open models ranked by how smart a model your hardware can actually run,
not just the biggest name - an efficient model can beat a larger one. Each one is marked **fits**,
**runs but slower**, or **too big**, based on your machine's memory. The list refreshes on demand,
never automatically, and works fine offline once you've picked one.

Managing what's already installed - pulling a model outside the curated list by its Ollama or
Hugging Face tag, or uninstalling one - lives on a separate **Models** page. Once you have at least
a few answers you trust (approved, or saved as a snippet), you can **benchmark** any installed
model against your current one there: it's scored on how closely it matches those approved answers,
so the choice is grounded in your own data rather than a leaderboard. The score is advisory, not
automatic - you still decide.

## Running a council

A council runs up to three open models on the same question in parallel and combines their answers
into one stronger result - the first model you pick is the **lead**, which synthesizes what the
others came back with. You configure it wherever you choose compute, in setup or under
**Settings → Model**.

A council is genuinely useful once you have the memory for it: below roughly 24GB, your machine
only runs one model at a time, since council members share the same memory bandwidth and compete
for it. A multi-model council runs at full speed when each model gets its own compute - your own
cloud GPU, or an attached inference provider.

Organizations have one more option: **council reviewers**, extra models an admin provisions (each
on its own GPU) purely to critique the lead's answer before the team sees it.

## Your own cloud, for a bigger model

If your machine can't hold the model you want, connect your own cloud account (a GPU on RunPod,
Lambda, or similar) under **Settings → Model**, and Anthill provisions and runs it there instead -
still your account, still your model, just bigger compute. This is separate from inference-provider
escalation below: your cloud model runs every answer, not just the hard ones.

## Escalating your hardest questions

Attach an inference provider (Berget, Groq, or Infercom) and only the questions your local or cloud
model can't handle well escalate to a larger hosted open model - it either asks you first each time
or escalates automatically, your choice. Your documents are never sent, only the single question,
stripped of personal information, and every escalation is logged. It's off until you attach a
provider.

## Training your own model

As you and your team work, approved answers become training data, in three tiers: **bronze** is
every answer Anthill generates, collected automatically but untouched; **silver** is an answer
someone else reused, a sign it was useful beyond the person who asked; **gold** is an answer a
human approved - a thumbs-up in chat, a saved snippet, or an admin approving it in wiki review -
and gold is the only tier that actually trains a model.

A solo user fine-tunes on their own gold answers, on whatever compute they've connected. An
organization fine-tunes the one shared model on the team's gold - including answers promoted from
individual snippets once corroborated - using the org's connected GPU. Either way: connect a GPU or
local toolchain, accumulate enough gold answers, then turn training on. Trigger a run manually, or
let it run on a schedule as new gold accumulates. A newly trained model is only promoted if it beats
the one you're currently using - you always keep the weights either way, and can also export your
gold and silver data to fine-tune elsewhere with your own tools.
