# Chat: an empty chat says what it runs on and how to start

## Problem

A new chat was a blank grey pane. The only words on it came from a CSS rule ("Start a conversation below."), so
a screen reader never heard them, and nothing said which model would answer or where it runs. A new user had no
example of what to ask.

## Fix

1. A conversation with no messages shows a heading, "Ask your own intelligence", and one line under it naming
   the model and where it runs ("qwen2.5:3b - runs on this computer", or "Runs on your organisation's model" in an
   organisation chat).
2. Below it, four starters: "Add a document and ask about it" (a link to the wiki page), "What can you do?",
   "Explain something simply" and "Draft a message with me". The last three put text in the chat box and focus it.
   Nothing is sent until the user sends it.
3. The empty state is real page text, so assistive technology reads it. It hides itself with CSS
   (`.chat-messages:has(.msg)`) as soon as any message is in the conversation, loaded or added by the page script.
4. At phone width the starters stack in one column.

## Acceptance criteria

- A chat with no messages contains the heading, the model line and four starters; a chat with messages contains
  none of them.
- The model line names the model the chat will use (`ollama_model`, default `qwen2.5:3b`) and does not claim
  "this computer" for an organisation chat.
- Clicking a text starter fills the box and sends nothing.
- `tests/test_chat_empty_state.py`.
