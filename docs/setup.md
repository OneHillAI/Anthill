# Get started

<p>Install Anthill. One click; on a Mac, drag <code>Anthill.app</code> into Applications and open it. It starts a local server and opens the app window automatically. There's no URL to type. On first launch, enter an org name, your email, and a password, then choose how you'll use it:</p>

<ul>
  <li><strong>Just for yourself.</strong> Pick <strong>Just evaluating (solo)</strong>. You're in, with your own private space, nothing else to set up. Skip to <a href="/USING_ANTHILL">Using Anthill</a> whenever you're ready. Everything past this point is for standing up a shared team or organization.</li>
  <li><strong>For a team.</strong> Pick <strong>Set up my organization</strong> and continue below. You can switch from solo to an org later too, at any time, from <strong>Settings</strong>.</li>
</ul>

<p>The steps below are for admins setting up an organization. Work top to bottom, or skip any and come back to it later in <strong>Settings</strong>.</p>

## 1. Where it runs
<p>By default your organization runs on your local machine, and each teammate's. Chat, wiki, cache, and model all run on-device. No cloud needed to start. Under <strong>Cloud &amp; model</strong> you can instead run the model on a GPU in <strong>your own cloud</strong> account, turn on a <strong>council</strong> (up to three models answering together), or attach an <strong>inference provider</strong> so only your hardest questions escalate to a larger hosted model. This is off by default, and your documents are never sent.</p>
<p>To keep the shared wiki available even when your own laptop is off, you can also host the backend on a dedicated machine, on-prem or a VM in your own cloud, and point your team at it under <strong>Cloud &amp; model &rarr; Wiki</strong>.</p>

## 2. Seed the wiki
<p>The wiki is your org's knowledge. Fill it by uploading documents, dropping files in a watched <code>inbox/</code> folder, or asking Anthill to <strong>research topics into wiki pages</strong>. Research is the fastest way to go from empty to useful. Everything goes through <strong>Wiki &rarr; Review</strong> before it becomes shared knowledge.</p>

## 3. Capture how your team works (Skills)
<p>On the <strong>Skills</strong> page, describe something your org does repeatedly, in plain words. How to triage a ticket, how to format a report. Anthill drafts a reusable skill from it. Scope it to the org, a team, or just yourself. An agent picks the right one automatically when a task matches.</p>

## 4. Train your own model
<p>As the team works, good answers become training data. Mark a snippet, or approve a page. Once you have enough, turn on <strong>Train your model here</strong> under <strong>Cloud &amp; model &rarr; Training</strong> and start a run on your own GPU. A new model is promoted only if it beats the current one. You keep the weights either way.</p>

## 5. Connect your tools
<p>Slack, Notion, Jira, Google Drive, Microsoft 365, and others connect under <strong>Connectors</strong>. Add a server, approve who can use it. Each connection gets its own scoped, revocable access, and everything is logged.</p>

## 6. Invite your team
<p>Under <strong>Users</strong>, invite people by email and choose their role. Create <strong>Teams</strong> to give a group its own working wiki scope, while it still draws on the shared org wiki when needed.</p>

## Optional: sign-in and remote access
<ul>
  <li><strong>Single sign-on.</strong> Let people log in with Google or Microsoft instead of a password.</li>
  <li><strong>Remote access.</strong> Let off-network teammates reach the backend without setting up a VPN yourself. Go to <strong>Settings &rarr; Remote access</strong>, pick a provider, and start a secure tunnel.</li>
  <li><strong>Web search.</strong> Add a Google Custom Search key for higher-quality results. It works out of the box without one.</li>
</ul>

## Running this for real, not just trying it out
<p>Put the dashboard behind a reverse proxy with a real TLS certificate, rather than exposing it directly. Back up your secrets file too; Anthill tells you where it lives on first run, and losing it makes stored credentials unrecoverable. The official Mac release is already signed and notarized, so it opens without a security warning.</p>

<p><strong>Do not put the desktop app or the personal launchers behind a proxy or tunnel by accident.</strong> The desktop app, <code>start.sh</code>, <code>make alpha</code>, the macOS auto-start agent and <code>scripts/start.ps1</code> trust requests that come from the machine itself. A proxy or tunnel on the same machine that adds no forwarding header (a default nginx <code>proxy_pass</code>, HAProxy without <code>forwardfor</code>, or a TCP forward such as <code>ssh -R</code>, <code>socat</code> or <code>ngrok tcp</code>) makes a remote request look like one from the machine. Switch Remote access on first (Settings, provider "manual"), which makes the server shared, or, for the launchers (not the desktop app, which always marks itself local), set <code>ANTHILL_LOCAL_ONLY=0</code>. Run a server for other people with <code>anthill web</code> or the container, which are shared by default.</p>

<p>To check which version you are running, open <strong>Settings</strong> and go to the <strong>This device</strong> tab. The <strong>Version</strong> line is there.</p>
