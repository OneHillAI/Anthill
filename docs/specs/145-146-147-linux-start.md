# Spec: start.sh and the quick start work on a stock Linux box

Status: implemented. Lane: `pillar:platform`. Issues: #145, #146, #147.

## Problem

A first-time user on Debian followed the quick start and hit these in a row:

- `make install` failed with `make: command not found`; the requirements never mention `make` (#146).
- `start.sh` looked for Ollama only at `~/bin/ollama`. With Ollama installed the documented way (`/usr/local/bin`)
  or already running on `:11434`, it still tried to download the macOS app zip, failed, and told the user the
  internet was down (#145).
- `start.sh` only knew the macOS `open`, so the dashboard never opened, and the closing text said "Open in Chrome" and
  "double-click anthill.command" (#147).
- Without `lsof` a stale dashboard on `:8000` was not stopped, and nothing said so (#147).
- Nothing said that `start.sh` holds the terminal, so closing the window killed the app (#147).

While fixing the last point it turned out that Ctrl-C did not stop the dashboard either: a background job started
by a script ignores SIGINT, so only `start.sh` exited and the server kept running.

## Requirements

1. Ollama is the first of `ollama` on `PATH`, `~/bin/ollama`, `/usr/local/bin/ollama`.
2. A server already answering on `:11434` is enough; the binary is then optional. Without the binary the model is
   checked through `/api/tags`, and a missing `qwen2.5:3b` gets a message saying how to pull it, not an error.
3. The macOS app zip is downloaded only when `uname -s` is `Darwin`, and only when there is neither a binary nor a
   running server. The "no internet?" message stays for that download failing.
4. On any other OS with neither, `start.sh` prints the Ollama download page (`https://ollama.com/download`) for the
   user to follow, says why it stopped, and exits non-zero. It never installs Ollama itself and prints no
   command to pipe into a shell.
5. The browser opens with `open` on Darwin and `xdg-open` where present; otherwise the URL printed in the closing
   text is all there is. Messages no longer mention Chrome, and `anthill.command` is named only on Darwin.
6. A stale dashboard on `:8000` is found with `lsof`, else `fuser -n tcp`, else `ss -ltnp`. With none of them the
   script warns and carries on.
7. Ctrl-C (or SIGTERM) stops the dashboard that `start.sh` started. `--no-wait` still detaches.
8. The text printed while holding the terminal says to keep it open, that Ctrl-C stops Anthill, and that
   `--no-wait` detaches. README and CONTRIBUTING say the same.
9. README and CONTRIBUTING list `git` and `make` as requirements, give `sudo apt install git make` for Debian and
   Ubuntu, the Linux Ollama install line, and the make-free equivalent of `make install`.
10. Log files go to `$ANTHILL_LOG_DIR` (default `/tmp`, so unchanged for users). Tests set it to a temp dir.

## Known limits

- Tested with stubbed tools on macOS only; no real Linux, `xdg-open`, `fuser` or `ss` was run.
- `start.sh` still stops whatever listens on `:8000`, not only an old Anthill dashboard.
- `ss -p` only shows processes the user owns; a root-owned listener is not found.
- On Linux the Ollama install is left to the user because the official installer uses `sudo` and writes system
  files.
- The make-free line skips the `pip install -U pip` step that the Makefile runs.
- A GUI-less Linux box has no `xdg-open`; the URL is printed and the user opens it.

## Acceptance

- On Linux with no Ollama and nothing on `:11434`, `start.sh` prints the install one-liner, exits non-zero,
  and makes no download request.
- On Linux with `ollama` on `PATH`, or in `~/bin`, it is used and nothing is downloaded.
- With a server up and no binary, setup completes and reports whether `qwen2.5:3b` is present.
- On Darwin with neither, the app zip download is attempted.
- Linux calls `xdg-open URL` and not `open`; Darwin calls `open URL` and not `xdg-open`; `--no-wait` calls neither.
- A stale process on `:8000` is stopped using `lsof`, `fuser` or `ss`, whichever exists; with none, a warning.
- SIGINT to `start.sh` ends the dashboard process.
- Tests: `tests/test_start_sh.py` runs the real script against stub tools (model-free, nothing installed,
  downloaded or started; the only process killed is a `sleep` the test spawned).
