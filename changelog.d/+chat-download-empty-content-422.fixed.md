**`/chat/download` no longer answers a raw validation error for empty content.** A missing or
empty `content` field used to trip FastAPI's own request validation (`422`) before the route's own
"nothing to export" check ever ran. Both now reach that check and get the same friendly `400`.
