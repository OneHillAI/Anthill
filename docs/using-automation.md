# Automating work

## Tasks

A **task** is work Anthill does on a schedule or on demand. You describe it in plain words, like a
daily digest or a weekly report. Tasks keep your local time zone, including through daylight
saving. Run one immediately, or let it run on its own schedule.

**Cancel future runs** stops a task from running again. Anything it already did stays, the history is kept and
nothing is deleted, and a run already in progress is not interrupted. **Reactivate** turns a cancelled task's
schedule back on. It does not run anything at that moment: work that fell due while the task was cancelled is
skipped and the task waits for its next scheduled time, and follow-ups you queued before cancelling wait for
that run too. You can reactivate a task once any run you cancelled has finished. **Run now** and **Add follow-up**
are not available on a cancelled task; reactivate it first.

A task needs a model that can use tools to search the web, open files or take actions. If your model cannot (a
small model such as gemma3:4b, for example), the task still runs and answers from its description and the files
you attached, and the result begins with a note saying no tools were used. Because it did not do any work outside
the answer itself, the run is marked "needs review" and you are notified each time. Choose a model that supports
tools for work that needs them.

## Agents

An **agent** is a named worker with a standing job, like an employee with a role. It runs toward
that job on its own, using your wiki, memory, and skills, and reports back. Anything consequential
is held for your approval first. Sending an email, or writing to a shared wiki: none of it happens
silently.

Results are checked automatically before you see them. Anything that doesn't hold up is flagged for
your review, not presented as fact. If you can edit the task, choose Mark reviewed once you have
checked that result. The Tasks list warning clears, and the page records who reviewed it and when.
The original check stays in the run history, so a later result that needs review raises its own warning.

## Skills

A skill is a reusable instruction set for something you do repeatedly. Anthill can propose one on
its own, after an agent does a good job. Or you can write one directly. Skills are scoped to you, a
project, or the whole org. An agent picks the right one automatically when a task matches.

## Staying informed

A notification bell in the sidebar collects what needs you: a finished task, an agent paused for
approval, a project invite. Nothing depends on you remembering to check.
