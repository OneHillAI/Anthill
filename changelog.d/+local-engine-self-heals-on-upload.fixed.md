**Uploading a document now works even if your local AI engine had stopped.** If the engine behind
your local model wasn't currently running - crashed, quit, or just never restarted after your
machine slept - uploading a document used to fail outright, and the only way to fix it was knowing
to go start the engine yourself. Anthill now tries to restart it automatically before reading your
document, the same way it already does before downloading a model; most of the time the upload now
just succeeds, with no error and no action needed from you. If it genuinely can't be started (no
local engine installed, or a misconfigured connection), you now get a plain "check your model
setup" message with a link to Settings, instead of a technical error asking you to answer your own
question.
