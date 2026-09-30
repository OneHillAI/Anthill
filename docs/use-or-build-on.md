<h1>Two ways to use Anthill</h1>

<p>Anthill is <strong>one</strong> open-source product, with two front doors depending on what you want
to do. There is no "lite" edition and no separate "raw" version: both paths lead to the same AGPL v3
codebase. Pick the door that matches your goal.</p>

<p><em>(This is about <strong>what you want to do</strong>, not about price. The whole product is free and
open source either way; the optional hosted/enterprise offering is a separate choice, covered in the
<a href="../README.md">README</a>.)</em></p>

<h2>Use it - install and run</h2>
<p>For an organization, a team, or anyone who just wants to run their own AI. One click, no Terminal: you
install Anthill like any Mac app, create your org, and start working. Your knowledge and your model stay
yours.</p>
<ul>
  <li><strong>Start here:</strong> the <a href="setup.md">Setup guide</a> (install, create your org, invite
  your team).</li>
  <li><strong>What it does and how:</strong> <a href="how-it-works.md">How it works</a>.</li>
</ul>

<h2>Build on it - run from source and extend</h2>
<p>For developers who want to run Anthill from source, extend it, or build something of their own on top.
Same product, just the developer door.</p>

<p><strong>Run it from source</strong> (the dev loop):</p>
<pre><code>git clone https://github.com/OneHillAI/Anthill.git
cd Anthill &amp;&amp; bash start.sh</code></pre>
<p>Full developer setup, environment variables, and the test suite are in
<a href="../CONTRIBUTING.md">CONTRIBUTING.md</a>.</p>

<p><strong>Extend it</strong> through the supported surfaces - no fork required:</p>
<ul>
  <li><strong>Skills</strong> - package a reusable capability as a <code>SKILL.md</code>. The
  lowest-friction extension; no core knowledge needed.</li>
  <li><strong>MCP servers</strong> - connect tools and data sources (Slack, Notion, Drive, and more) as
  Model Context Protocol servers, added and admin-approved in the dashboard.</li>
  <li><strong>Training backends</strong> - add a GPU target (a cloud, a neocloud, or your own box) by
  implementing one backend class against the training-backend interface.</li>
  <li><strong>Node / orchestrator API and the CLI</strong> - drive Anthill programmatically or wire it
  into your own systems.</li>
</ul>
<p>The internals these build on are described in <a href="how-it-works.md">How it works</a> and the
<a href="../ARCHITECTURE.md">Architecture</a>.</p>

<p><strong>Start your own</strong> - want to run a project the way Anthill runs itself (AI agents under
human direction, governed and transparent)? That practice is <strong>ASDD</strong>, packaged as
a template you can adopt: <a href="https://github.com/OneHillAI/ASDD">OneHillAI/ASDD</a>.
See the README's <a href="../README.md">"How it's built"</a> section.</p>

<p><strong>Contribute back</strong> - improvements, integrations, and skills are welcome. Start with
<a href="../CONTRIBUTING.md">CONTRIBUTING.md</a> and the <code>good first issue</code> label.</p>

<h2>Not sure which?</h2>
<ul>
  <li>You want your org to <em>have</em> an AI it owns -> <strong>Use it</strong> (<a href="setup.md">Setup
  guide</a>).</li>
  <li>You want to <em>build with or on</em> Anthill -> <strong>Build on it</strong>
  (<a href="../CONTRIBUTING.md">CONTRIBUTING.md</a> + the surfaces above).</li>
</ul>
<p>Both are the same product. You can start by using it and move to building on it any time.</p>
