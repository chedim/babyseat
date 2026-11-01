
## One chat per person (#770)

`POST /session` as a person returns that person's existing chat (a new one only if they have none); loop sessions are still new each time. `migrate_one_chat(sessions, keep, operator)` stamps the operator's chosen chat as theirs and marks their other chats `readonly` (idempotent; still readable). Posting a message to a readonly chat is refused with 409 and the person's chat id (`chat` / `sessionID`). Watch escalations go to the event owner's chat, not the newest chat.
