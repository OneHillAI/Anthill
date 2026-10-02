#!/usr/bin/env node
// Assembles docs-site/docs/ (generated, gitignored) from an explicit whitelist of files in the
// repo root and docs/, before `docusaurus build` / `docusaurus start` runs. This is a whitelist,
// not an exclude-list: docs/ also holds internal "audience: the engineering agent" implementation
// specs, ops runbooks, and docs/specs/** (issue-numbered engineering specs) that must never reach
// docs.anthill.run. Adding a page here is how you publish it; anything not listed stays private.
//
// docs.anthill.run is for people USING the app (solo or admin), not developers - that's what the
// GitHub repo's own README/CONTRIBUTING are for. An earlier version of this whitelist published
// the repo README (a "clone and build from source" page) as the site's own homepage, plus a pile
// of contributor/ops/spec docs (architecture, release signing, auto-update internals, the alpha
// test guide, the dev council) under the same "Guides" nav as end-user content. Keep this list to
// pages an actual user of the app needs; a page for contributors belongs in the repo, not here.

const fs = require("fs");
const path = require("path");

const REPO_ROOT = path.resolve(__dirname, "..", "..");
const DEST = path.resolve(__dirname, "..", "docs");

// [source path relative to repo root, dest filename relative to docs-site/docs/]
const FILES = [
  ["docs/DOCS_OVERVIEW.md", "README.md"],
  ["docs/how-it-works.md", "how-it-works.md"],
  ["docs/setup.md", "setup.md"],
  ["docs/USING_ANTHILL.md", "USING_ANTHILL.md"],
  ["docs/using-chat-and-knowledge.md", "using-chat-and-knowledge.md"],
  ["docs/using-automation.md", "using-automation.md"],
  ["docs/using-models-and-compute.md", "using-models-and-compute.md"],
  ["docs/using-connectors-and-personalization.md", "using-connectors-and-personalization.md"],
];

fs.rmSync(DEST, { recursive: true, force: true });
fs.mkdirSync(DEST, { recursive: true });

for (const [src, destName] of FILES) {
  const srcPath = path.join(REPO_ROOT, src);
  if (!fs.existsSync(srcPath)) {
    throw new Error(`collect-docs: whitelisted file is missing: ${src}`);
  }
  fs.copyFileSync(srcPath, path.join(DEST, destName));
}

console.log(`collect-docs: published ${FILES.length} pages to docs-site/docs/`);
