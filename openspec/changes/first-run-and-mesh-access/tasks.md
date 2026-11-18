# Tasks

- [x] Setup token for the first account (`install_scope.py`, `/setup`, single sign-on, start-up console line).
- [x] Mesh token on the orchestrator read routes; the wiki agent sends it.
- [x] `require_mesh_unless_local` on the node agent routes; `anthill serve` refuses an unprotected bind.
- [x] Reset link on the page only for the machine itself on a one-account local server.
- [x] Tests for every route and caller kind; existing tests updated to use a local caller.
- [x] Docs (`docs/setup.md`) and a changelog fragment.
- [ ] Human review (`anthill/node_agent/**` is a protected path).
