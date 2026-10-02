# Models & compute

## Choosing your model

Anthill lists the current open models, ranked by how smart a model your hardware can actually run.
Not just the biggest name: an efficient model can beat a larger one. Each one is marked **fits**,
**runs but slower**, or **too big**, based on your machine's memory. The list refreshes on demand,
never automatically, and works fine offline once you've picked one.

Managing what's already installed lives on a separate **Models** page. Pull a model outside the
curated list by its Ollama or Hugging Face tag, or uninstall one. Once you have at least a few
answers you trust, approved or saved as a snippet, you can **benchmark** any installed model
against your current one there. It's scored on how closely it matches those approved answers, so
the choice is grounded in your own data, not a leaderboard. The score is advisory. You still decide.

## Running a council

A council runs up to three open models on the same question in parallel, and combines their
answers into one stronger result. The first model you pick is the **lead**, which synthesizes what
the others came back with. Configure it wherever you choose compute, in setup or under
**Settings &rarr; Model**.

A council is genuinely useful once you have the memory for it. Below roughly 24GB, your machine
only runs one model at a time, since council members share the same memory bandwidth and compete
for it. A multi-model council runs at full speed when each model gets its own compute: your own
cloud GPU, or an attached inference provider.

Organizations have one more option. A **council reviewer** is an extra model an admin provisions,
each on its own GPU, purely to critique the lead's answer before the team sees it.

## Your own cloud, for a bigger model

If your machine can't hold the model you want, connect your own cloud account, a GPU on RunPod,
Lambda, or similar, under **Settings &rarr; Model**. Anthill provisions and runs the model there
instead. It's still your account and still your model, just bigger compute. This is separate from
inference-provider escalation below. Your cloud model runs every answer, not just the hard ones.

## Escalating your hardest questions

Attach an inference provider, Berget, Groq, or Infercom, and only the questions your local or cloud
model can't handle well escalate to a larger hosted open model. It either asks you first each time
or escalates automatically. You choose. Your documents are never sent, only the single question,
stripped of personal information first. Every escalation is logged. It's off until you attach a
provider.

## Training your own model

As you and your team work, approved answers become training data, in three tiers. **Bronze** is
every answer Anthill generates, collected automatically but untouched. **Silver** is an answer
someone else reused, a sign it was useful beyond the person who asked. **Gold** is an answer a
human approved: a thumbs-up in chat, a saved snippet, or an admin approving it in wiki review. Gold
is the only tier that actually trains a model.

A solo user fine-tunes on their own gold answers, on whatever compute they've connected. An
organization fine-tunes the one shared model on the team's gold, including answers promoted from
individual snippets once corroborated, using the org's connected GPU. Either way, the steps are the
same: connect a GPU or local toolchain, accumulate enough gold answers, then turn training on.
Trigger a run manually, or let it run on a schedule as new gold accumulates. A newly trained model
is only promoted if it beats the one you're currently using. You always keep the weights either
way, and you can export your gold and silver data to fine-tune elsewhere with your own tools.
