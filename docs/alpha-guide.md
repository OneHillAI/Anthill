# Anthill - Alpha test guide

A step-by-step walkthrough to install, open, and test the alpha end-to-end on a Mac. Written for a
non-engineer: no Terminal, no commands. Each step says what you should **see** so you can confirm it
worked.

Anthill is a self-contained Mac app. It bundles everything it needs (the local AI engine and its
runtime), runs entirely on your machine, and stores your data under
`~/Library/Application Support/Anthill/`. Nothing leaves your Mac unless you explicitly turn on a
feature that reaches out (and the app tells you when that is the case).

Time: about 15 minutes.

---

## 0. What you're testing (and what isn't ready yet)

**Ready to test now:** the local AI dashboard - chat, a knowledge wiki that grows, saving "snippets",
scheduled/automatic agent tasks, agents, memory, settings, metrics and the audit log, and notifications.

**Not in this build yet (so don't expect them):**

- Instant email/Slack push (IMAP/webhook), the automated model-training run on AWS, and Microsoft
  sign-in. These are queued next.
- **Semantic cache and semantic knowledge search.** This build ships a lean install that does not yet
  include the embedding model those features need, so it falls back to keyword matching. In practice:
  the Metrics page will show no cache savings (it says so on the page), and answers drawn from your wiki
  are matched by keyword rather than by meaning. A follow-up build adds a small embedding model (served
  by the bundled engine) that switches both on. Everything else works normally in the meantime.

---

## 1. Install (one-time)

1. Open the **Anthill.dmg** you were given (or downloaded from anthill.run/download).
2. In the window that opens, **drag the Anthill icon onto the Applications folder**.
3. Open **Applications** and double-click **Anthill**.
4. The official release is signed and notarised, so it opens normally with a double-click. (If you were
   handed an unsigned local build instead, macOS may warn the first time that it is from an unidentified
   developer - **right-click the Anthill app -> Open -> Open**, and after that it opens normally.)

**You should see** the Anthill window open on its own - no Terminal, no browser, no separate downloads.
The app carries its own AI engine.

---

## 2. First-run setup

On first launch you land on a short **setup wizard** (three steps).

1. **Create your account** - your email, an optional name, and a password (12+ characters; remember it).
   Click **Get started**. (Setting up for a team? Create your personal account first - you can create an
   organisation later from the dashboard. There is no separate admin invite needed to begin.)
2. **Solo or organisation** - choose **Solo** to try it as one person on this Mac, or **Organisation**
   if you want a shared backend and to invite others later. Solo is the quickest way to test.
3. **Choose your AI** - pick the suggested local model (Anthill sizes it to your Mac). The first model
   downloads once (roughly 2 GB); you will see progress. When it finishes, setup is complete.

**You should see** the **Dashboard**, signed in. The system is live and running entirely on your machine.

---

## 3. The end-to-end test

### 3a. Chat
- Click **Chat** -> start a conversation -> ask anything, e.g. *"Explain what a semantic cache is in one
  paragraph."*
- **You should see** the answer stream in, word by word. The header reads "Running on this machine.
  Nothing leaves it." (Web search is a per-chat option under **Options**, off by default on Solo - turning
  it on sends that query to a search engine, and the header updates to say so.)

### 3b. Give it knowledge, then ask about it
- Click **Wiki** -> **Add a document** -> choose a `.md`, `.txt`, `.pdf`, or Office file (`.docx`,
  `.pptx`, `.xlsx`) and upload it.
- Anthill reads the document and writes it up as a wiki page. A clean page is filed automatically; if it
  needs a look, it waits in your **review queue** (you will see it under the wiki's Review tab / the
  suggestions inbox) - approve it there.
- **You should see** the new page appear under **Wiki -> Pages** once it is filed/approved.
- Now go back to **Chat** and ask a question the document answers. **You should see** the answer drawn
  from your document, with the source page cited beneath it.

> Note for this build: retrieval is keyword-based (see section 0), so phrase the question with words that
> appear in the document. Semantic recall arrives with the embedding-model follow-up.

### 3c. Save a snippet
- In a chat answer, **select some text** with your mouse -> a **Save snippet** button appears -> click it,
  add a tag, and save.
- **You should see** a confirmation with the model's one-line reason it is worth keeping. Click
  **Snippets** in the sidebar and your snippet is there, tagged and marked personal (it becomes org-wide
  only if a second person saves the same thing).

### 3d. Create an automatic task
- Click **Tasks** -> **New task** -> use an example or type a goal -> choose **Run once** and tick **Run
  immediately** -> create it.
- **You should see** it appear in the list; within a minute its status moves to **done** and a **Result**
  becomes available. (Tasks use the local model; simple goals work best in the alpha.)

### 3e. Look around
- **Agents** - a named worker with a standing mandate; consequential actions are cross-checked by a
  second model and held for your approval.
- **Memory** - durable facts the assistant remembers and recalls into answers.
- **Metrics** - query count, adoption, latency (and cache/savings, which stay at zero in this build - the
  page explains why; see section 0).
- **Audit log** - every login and action.
- **Settings** - your model, privacy options (including the web-access toggle), and, for an organisation,
  the backend and members.

### 3f. Turn on notifications
- Sidebar -> **Enable notifications** -> allow when prompted.
- **You should see** a test notification confirming they work.

### 3g. Prove it survives a restart
- **Quit Anthill** (Cmd-Q) and open it again.
- **You should see** you are still signed in and your data is intact - the keys and data persist under
  `~/Library/Application Support/Anthill/`.

---

## 4. Optional: connect your tools

External tools (Slack, Notion, Jira, Google Drive, and more) connect via **Connectors** in the
dashboard: add the service, approve it, and grant an agent access. No restart needed. Google sign-in and
some connectors need one-time credentials; the Connectors screen walks you through each.

---

## 5. Stop / restart / reset

- **Quit:** Cmd-Q (or Anthill -> Quit).
- **Restart:** open Anthill again from Applications.
- **Start fresh (wipe all data):** quit Anthill, delete the folder
  `~/Library/Application Support/Anthill/`, then open Anthill again (this deletes your account, chats,
  wiki, and snippets - only for a clean re-test).

---

## 6. Troubleshooting

| Symptom | Fix |
|---|---|
| "Unidentified developer" on first open | Right-click the app -> Open -> Open (once). |
| The window is blank or "starting" | Give it a few seconds on first launch (it starts its engine); if it persists, quit and reopen. |
| Chat is slow | The first answer after launch is the slowest. A small model is fast but modest; pick a larger one in **Settings -> model** if your Mac is strong. |
| Forgot your password | Quit, delete `~/Library/Application Support/Anthill/`, reopen (creates a fresh account - for re-testing only). |

---

## 7. Known alpha limitations (so nothing surprises you)

- **Semantic cache and semantic wiki search are off in this build** (no embedding model yet) - retrieval
  is keyword-based and the Metrics cache-savings stay at zero. A follow-up build switches both on.
- **Inviting teammates:** the invite link is shown in the app; send it to the person yourself (it is not
  emailed yet).
- **Microsoft sign-in** is not built (email/password works; Google is configurable).
- **Instant email/Slack reactions** and the **automated AWS model-training run** are the next builds, not
  testable in this version.

That's the full alpha. Everything above runs on your machine.
