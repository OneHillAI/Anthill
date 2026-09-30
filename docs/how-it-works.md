# How Anthill works

<p>Anthill is your own AI, solo or for a whole organization. It runs on open-weight models, in your environment, accessible only to you (or your org) and trained only on your own work. The knowledge it builds and the model it becomes are yours to keep. This page explains how the pieces fit together; for the deep internals see <a href="https://github.com/OneHillAI/Anthill/blob/main/ARCHITECTURE.md">Architecture</a> on GitHub.</p>

## One assistant, in your perimeter
<p>Every person on the team runs a local <strong>node</strong> on their own machine: it does inference, keeps a private cache, and talks to your org's shared backend. The <strong>org backend</strong> - by default the machine an admin runs Anthill on, or a dedicated <strong>always-on</strong> machine (on-prem, or a persistent VM in your own cloud / VPC) when you want it independent of any laptop - hosts the <strong>shared wiki</strong>, the review queue, the cross-machine index, and routing. Everyone connects to it, so knowledge is served live: an approved page shows up for the whole org on their next question, with nothing to copy or sync.</p>
<p>Prompts, answers, documents, and model weights stay inside your perimeter. Chat uses web search by default, so when the assistant decides a search helps, the <strong>search query</strong> (never your documents) leaves your perimeter; switch a chat to <strong>local + wiki only</strong> (under Options) to keep everything in-perimeter. The other optional outbound is <strong>inference-provider escalation</strong> - a single PII-scrubbed question sent to a hosted open model for your hardest turns - off unless you attach one. Both are covered below.</p>

## Where it runs, and how far it can reach
<p>You choose where the model runs, and neither choice sends us anything:</p>
<ul>
  <li><strong>Your machine</strong> - the model runs on this device: small-to-mid open models, full offline privacy, no monthly bill.</li>
  <li><strong>Your cloud</strong> - Anthill provisions your chosen model on a private GPU in your own cloud account (you connect once with an API key), for models bigger than a laptop can hold. Only the model runs there; your wiki, memory, and files are never uploaded by that choice.</li>
</ul>
<p>A <strong>council</strong> can run up to three open models in parallel and combine them into one stronger answer - the first you pick is the lead that synthesises the rest. And because even a rented GPU cannot hold today's frontier-scale open models, you can optionally <strong>attach an inference provider</strong> (Berget, Groq, or Infercom) that hosts those: only your hardest questions escalate to it - it either asks you first each time or escalates automatically - while everything else keeps running on your machine or cloud. Attaching one is off by default.</p>

## The chat is the front door
<p>Chat is one box. You type what you want and Anthill reads it and picks the approach, so there are no modes to choose first:</p>
<ul>
  <li><strong>A question</strong> is answered from your wiki and cache, generated locally when it is new. Web search is in play by default: on a capable model the assistant plans its own search and runs one when it decides a search helps (only the query leaves; switch to local + wiki only to keep everything in-perimeter). A small local model falls back to a basic mode and the chat says so.</li>
  <li><strong>A "make" or "do" request</strong> ("draft an onboarding email", "give me this as a spreadsheet") comes back as a short <strong>proposal</strong> you confirm in chat with <em>Do it / Edit / Cancel</em>. Confirm and the agent produces the file in the format you named.</li>
  <li><strong>A recurring request</strong> ("every morning summarize my inbox") proposes a scheduled <strong>task</strong>; confirm and it lands on the Agent tasks page. Any answer can also be saved as a recurring task.</li>
</ul>
<p>Because every side-effect is proposed and confirmed before it runs, the chat is both the easiest surface and a safe one.</p>

## Knowledge that compounds
<p>Four layers turn everyday use into a durable asset, checked fastest-first on every question:</p>
<ol>
  <li><strong>Semantic cache.</strong> Each question becomes a numerical fingerprint and is matched against past questions on your machine. A close-enough match returns the previous answer instantly, with no model run. A correctness guard prevents a near-miss (for example "EU refund policy" vs "US refund policy") from returning the wrong answer.</li>
  <li><strong>Org index.</strong> If another machine in your org already answered it, the answer comes back from the shared index without that machine being involved. Only fingerprints, never prompt text, live in the index.</li>
  <li><strong>Memory.</strong> Durable facts, decisions, and preferences are distilled automatically from chats and finished tasks, then recalled into future answers. Personal by default; an admin can promote a fact to the whole org.</li>
  <li><strong>The wiki.</strong> When you ingest a document the model reads it and writes a structured page: a title, a one-line summary, the key facts, and links to related topics. Good answers can be filed back as pages too. The model maintains the wiki itself, and every page enters the shared org wiki only after a human approves it in <strong>Wiki review</strong>.</li>
</ol>
<p>If none of those answer it, the best-routed local model generates a fresh answer on your hardware and caches it for next time. Starting from an empty wiki, an admin can use <strong>Research topics into wiki pages</strong> to have Anthill research one or more topics on the web and file cited, verified drafts into the review queue.</p>

## Work that runs itself
<ul>
  <li><strong>Agent.</strong> When a request needs tools, the assistant runs a multi-step agent that can search the wiki and the web, read and draft, create files, and call approved connectors, under a governed identity with scoped permissions and a full audit trail.</li>
  <li><strong>Tasks.</strong> The same agent on a schedule or on events: a daily inbox digest, weekly competitor research, a report filed to the wiki. You describe it in plain words and confirm.</li>
  <li><strong>Skills.</strong> Reusable instruction sets the agent loads on its own when a task matches, scoped Organization / Team / Personal, so your organization's way of doing something is followed consistently.</li>
  <li><strong>Proactive maintenance.</strong> In the background the agent drains the <code>inbox/</code> folder (drop a file, it gets ingested), lints for broken links and orphan or duplicate pages, and proposes wiki promotions into the review queue.</li>
</ul>

## A model that becomes yours
<p>Answers worth keeping become <strong>gold</strong> training data (mark a snippet, or approve a page), quality-tiered and scoped personal or org. Your org can then fine-tune an open-weight model on that gold using <strong>your own GPU</strong> (on-prem, your own cloud account, or your own neocloud account). The training GPU spins up only for the run and tears down right after; only PII-scrubbed gold is used, and a new model is promoted only if it beats the current one on your evaluations. You keep the weights, and when a stronger open model ships you move onto it while your wiki and training carry across.</p>

## When it reaches out, and when it does not
<ul>
  <li><strong>Web search</strong> blends current web results into an answer (and they can be filed back into the wiki). Only the search query leaves your perimeter; the answer is written locally.</li>
  <li><strong>Inference-provider escalation</strong> can send a single PII-scrubbed question to a hosted, frontier-scale open model (Berget / Groq / Infercom) for your hardest turns. It is off unless you attach a provider, question-only, and never sends your documents.</li>
</ul>
<p>With both off, Anthill runs fully offline: chat, wiki, cache, and local generation all keep working with the internet down.</p>

## Governed by design
<ul>
  <li>Every wiki write goes through the <strong>review gate</strong> before it is shared.</li>
  <li>Agents act under named <strong>identities with scoped permissions</strong>, and every tool call is recorded in the <strong>audit log</strong>.</li>
  <li>External tools are <strong>MCP servers</strong> an admin approves; each consumer gets its own scoped, revocable token, and nothing runs without a logged authorization.</li>
  <li>Side-effects in chat are <strong>proposed and confirmed</strong> before they run.</li>
</ul>

## What your organization can do with it
<ul>
  <li>Answer "how do we do X here" from your own documents and decisions, instead of re-explaining or digging through drives.</li>
  <li>Stand up a knowledge base from scratch by researching topics into reviewed, cited pages.</li>
  <li>Automate recurring work: inbox digests, research roundups, reports filed to the wiki.</li>
  <li>Draft documents, spreadsheets, and decks in the format you ask for, grounded in your knowledge.</li>
  <li>Connect your existing tools (Slack, Notion, Jira, Drive, and more) through governed MCP connectors.</li>
  <li>Train and keep your own model that gets better as your team works, without your data or weights ever leaving.</li>
</ul>

<p>New here? See <a href="/setup">Get started</a> for how to set up Anthill, solo or for a team.</p>
