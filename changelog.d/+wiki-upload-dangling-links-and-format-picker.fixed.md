**Document upload on Knowledge now actually works on a fresh wiki.** The AI summariser's own
"## Related" cross-references always point at pages that don't exist yet on a new or sparse wiki -
a mechanical check was treating the model's own generated links as broken and silently queuing
every upload for review instead of publishing it, so the first document you ever add could never
land. Those links are now neutralised (kept as plain text, or the whole section dropped if it turns
out to be nothing but dangling links) instead of blocking the upload; a genuinely broken link you
type yourself is still caught. Also: the upload file picker was blocking Word, PowerPoint, Excel,
and HTML documents even though the server already supported them - it now accepts everything the
backend does.
