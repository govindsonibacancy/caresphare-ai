# CareSphere AI — Conversation History & Persistent Chat UX

Phase 17 completes the user-facing persistent conversation experience:
Phase 13 already built conversation ownership and message persistence,
and Phase 16 already built the chat UI, but nothing let a user see their
past conversations, reopen one, or keep a conversation open across a
browser refresh. This phase adds exactly that - two small, read-only
backend endpoints and a chat sidebar/routing layer on top of them -
without altering any Phase 1-16 authorization, retrieval, or generation
behavior.

## Audit findings (before writing any code)

Inspected directly, not assumed:

- **No conversation-list or conversation-detail endpoint existed.**
  `app/api/rag.py` was the only router touching conversations at all, and
  only ever via `POST /api/rag/answer`'s `conversation_id` field. `grep`
  for "conversation" across `app/api/*.py` confirmed this.
- **No `title` field existed** on `conversations` (migration 0019) - a
  conversation had no human-readable label at all.
- **No `sources` were ever persisted** with a message.
  `conversation_messages` (migration 0019) stored only `content`/`role` -
  Phase 13's `load_bounded_history` only ever needed the text for the
  LLM's prompt, never the citations. This meant a reloaded historical
  assistant message would have had no way to show which sources backed it.
- **`conversation_repository.list_recent_messages`** already existed and
  already implements exactly the bounded-history query Phase 17's detail
  endpoint needs (same `max_turns` window Phase 13's LLM context uses) -
  reused unchanged in shape, extended only to also select `sources`.
- **Phase 16's frontend held `conversationId` in local React state only**,
  never in the URL - a refresh always started a new conversation, and
  there was no way to link to, or list, a past one.

These four gaps (list endpoint, detail endpoint, title, persisted
sources) were the entire scope of what Phase 17 needed to add on the
backend. Everything else - ownership checking (`resolve_conversation`),
bounded history loading, message persistence, the `conversation_max_turns`/
`conversation_max_message_chars`/`conversation_max_context_chars`/
`conversation_retention_days` limits, and Phase 16's message/citation
rendering components - was reused completely unchanged.

## Backend changes

### Database (migration 0021, additive only)

```sql
ALTER TABLE conversations ADD COLUMN title TEXT;
ALTER TABLE conversation_messages ADD COLUMN sources JSONB NOT NULL DEFAULT '[]'::jsonb;
CREATE INDEX idx_conversations_user_id_last_activity_at ON conversations (user_id, last_activity_at DESC);
```

No table was recreated, no existing migration was touched, no data was
reset. Both columns are backward compatible: `title` is `NULL` until a
conversation's first turn completes; `sources` defaults to `[]` (correct
for every pre-Phase-17 row, and for every `USER` message, which never has
one).

### New endpoints

```
GET /api/conversations              -> Page[ConversationSummary]
GET /api/conversations/{id}         -> ConversationDetail
```

(`app/api/conversations.py`, `app/schemas/conversations.py`.) Both use
`get_user_scope` (authentication only, no special permission - matching
`/api/rag/answer`'s own model: every authenticated role can already have
a conversation, so every authenticated role can list/read their own).
`ConversationSummary` reuses the existing `Page[T]`/`PageParam`/
`PageSizeParam` pagination convention (`app/schemas/pagination.py`,
`app/api/_clinical_common.py`) unchanged - a conversation *list* has no
existing bound (unlike a conversation's *message count*, which
`conversation_max_turns` already bounds), so it genuinely needed real
pagination, reusing the exact mechanism every other list endpoint in this
codebase already uses.

`ConversationMessage.sources` reuses `app/schemas/rag.py`'s
`SourceReference` directly - never a second, parallel source model.

### Ownership (unchanged, reused exactly)

`GET /api/conversations/{id}` calls
`conversation_service.get_conversation_detail`, which calls
`resolve_conversation` - the *exact* function `POST /api/rag/answer`
already used for the same ownership check since Phase 13. A conversation
that doesn't exist and one that belongs to someone else both raise
`ConversationNotFound`, mapped to an identical `404`
("Conversation not found.") - the same enumeration-protection convention
every other phase already established. **No new authorization logic was
introduced** - this endpoint is a second *caller* of Phase 13's existing
ownership check, not a second implementation of it.

`GET /api/conversations` is always self-scoped to `scope.user_id` +
`scope.hospital_id`, for every role including `SUPER_ADMIN` -
`SUPER_ADMIN`'s existing bypass in `_can_access_conversation` lets it
*open* a specific conversation by id when needed (unchanged, Phase 13's
own design), but listing is always "my own conversations," never
"everyone's."

### Conversation title

Deterministic, never an LLM call: `conversation_service._derive_title`
collapses whitespace in the conversation's first user message and
truncates it to 60 characters (with an ellipsis marker when truncated).
`record_turn` calls `set_title_if_unset` (an atomic
`UPDATE ... WHERE title IS NULL`) on every turn - a no-op after the first
one, so a conversation's title is permanently set by its very first
message and never silently changes later.

### Sources persisted with assistant messages

`app/llm/answer_service.py`'s existing `record_turn` call gained one new
argument: `assistant_sources=[source.model_dump(mode="json") for source
in response.sources]` - the exact same `SourceReference` list already
returned to the client in that turn's HTTP response, serialized once and
stored alongside the message. A `USER` message is never given a
`sources` value (always `[]|`).

## Frontend changes

- **Routing**: one line added to `frontend/src/app/routes.tsx` -
  `{ path: 'chat/:conversationId', element: <ChatPage /> }` alongside the
  existing `{ path: 'chat', element: <ChatPage /> }`. Both render the same
  component; `ChatPage` reads `useParams()` to know which conversation
  (if any) is active. No routing library was changed or added.
- **`ConversationHistory.tsx`** (new): a presentational sidebar listing
  conversation summaries grouped into Today/Yesterday/Previous
  (`groupConversations.ts`, a pure function, never re-sorting what the
  backend already ordered by `last_activity_at DESC`). Handles its own
  loading/error/empty states with the same visual language Phase 16
  already established (`chat-history-status`, `role="alert"`, a retry
  button).
- **`ChatPage.tsx`** (extended, not rewritten): the URL is now the single
  source of truth for which conversation is active. An effect keyed on
  `useParams().conversationId` fetches `getConversation(id)` whenever it
  changes (including on first mount, a `<Link>` navigation, or browser
  back/forward) and hydrates local message state from the response -
  never from `localStorage`, never reconstructed client-side. A
  `skipNextLoadRef` flag avoids one redundant re-fetch in the single case
  where the page already has the freshly-created conversation's one turn
  in memory (right after `askAi()` returns a brand-new
  `conversation_id` and the page updates the URL to match it via
  `navigate(..., { replace: true })`).
- **New Chat** now calls `navigate('/chat')` - the same URL-driven effect
  resets local state; it never calls a delete/archive endpoint (none
  exists, and none was added - "new chat" only ever means "stop looking at
  conversation X," never "delete conversation X").
- **Selecting a history item** calls `navigate('/chat/:id')` - plain
  React Router navigation, so back/forward work automatically with no
  manual `history` manipulation.
- **Mobile**: the existing 800px breakpoint (Phase 16) gained a
  `chat-history-toggle` button (visible only below that width) and a
  `chat-history-wrapper--open` class toggle - the sidebar becomes a
  collapsible drawer instead of a persistent column. No new UI dependency.
- **State**: still plain React `useState`/`useEffect` (this project has
  no React Query/Redux/other state library - Phase 16's own choice,
  reused unchanged). Conversation-list state, active-conversation state,
  and messages remain three clearly separate pieces of state, never
  duplicated into `localStorage` or the URL beyond the id itself.

## Conversation lifecycle (actual, as implemented)

```
/chat (no id)
  → user sends first message
  → askAi({ query, conversation_id: null })
  → backend creates a conversation, persists USER+ASSISTANT rows (+ sources, + title)
  → response.conversation_id
  → navigate(`/chat/${id}`, { replace: true }) - URL now carries the id
  → loadHistory() refetches GET /api/conversations - the new conversation appears, titled, at the top

/chat/:id (existing conversation, via history click, direct link, or refresh)
  → GET /api/conversations/:id (ownership re-checked fresh, every time)
  → messages (with sources) hydrate local state
  → user sends another message
  → askAi({ query, conversation_id: id }) - same conversation, same URL
  → loadHistory() refetches - recency ordering reflects the new activity

browser refresh on /chat/:id
  → same URL → same effect → same GET /api/conversations/:id → same conversation restored
```

## Security verification

- **Authentication**: `GET /api/conversations`/`GET /api/conversations/{id}`
  both require `get_user_scope` - the exact same authentication dependency
  every other endpoint uses (Phase 3, unchanged). An unauthenticated
  request gets a `401` -
  `test_unauthenticated_request_cannot_list_conversations`/
  `test_unauthenticated_request_cannot_read_conversation_detail`.
- **Ownership**: `test_another_users_conversation_is_not_exposed` and
  `test_cross_hospital_conversation_is_not_exposed` (through the real
  API) confirm a `404` with no message/title/source leaked in the
  response body for a conversation the caller doesn't own - reusing
  Phase 13's `resolve_conversation` unchanged, never a frontend-side
  check of any kind.
- **URL handling**: the URL carries only `conversation_id` - never
  message content, never a query string with sensitive data. Verified by
  inspection (no other data is ever interpolated into a route) and by
  `test_no_sentinel_values...`-style discipline already established in
  Phase 15 continuing to hold (no new sensitive logging was introduced).
- **Browser storage**: grepped `frontend/src/` for `localStorage`/
  `sessionStorage`/`dangerouslySetInnerHTML` after implementation - the
  only match is a code comment explaining that `localStorage` is
  deliberately *not* used. The conversation id lives in the URL (which
  the browser already manages) and in React state; message content and
  sources live only in backend-fetched React state, never persisted
  client-side.

## Sources/citations compatibility

Historical messages render through the *exact* Phase 16
`ChatMessageItem` component - `fromHistoryMessage()` in `ChatPage.tsx`
maps a `ConversationMessage` (id/role/content/sources) onto the same
`ChatMessage` shape a freshly-generated turn already produces, and both
flow into the same `<ChatMessageItem>`. There is no second renderer, no
second citation-parsing path, and no second source panel - a `[Source N]`
marker in a historical answer is matched against that message's own
persisted `sources` array using the identical `linkifyCitations`/
`parseSourceAnchor` logic Phase 16 built, and clicking it opens the
identical `SourcePanel`.

## Tests

Backend: 24 unit tests (`test_conversation_service.py`, 10 new) + 12 new
end-to-end tests (`test_conversation_history_e2e.py`) covering listing
(self-scoped, ordered, paginated, empty), detail retrieval (messages +
sources, a zero-message conversation), ownership (cross-user, cross-
hospital, nonexistent - all `404`), and title derivation.

Frontend: 20 new tests - `groupConversations.test.ts` (4),
`ConversationHistory.test.tsx` (7), `ChatPage.history.test.tsx` (9) -
covering history rendering/loading/empty/error states, selecting a
conversation (navigation + active highlighting + message loading),
direct-URL restoration (refresh-equivalent), an unauthorized/nonexistent
conversation's safe error state, New Chat preserving history, and the
history list refreshing after a new conversation is created. Existing
Phase 16 tests (`ChatPage.test.tsx`) were updated only to wrap
`<ChatPage />` in a `MemoryRouter` (now required since the component uses
`useParams`/`useNavigate`) and to mock the two new API functions with an
empty default - no existing assertion's meaning was changed.

## Known limitations

- **The pre-existing `AppShell` top-level sidebar (Chat/Documents/Profile
  navigation, built in Phase 1) does not collapse on narrow viewports.**
  This is unrelated, pre-existing shell code Phase 17 did not touch (see
  "minimal-diff rule") - only the *chat feature's own* history sidebar
  gained a mobile drawer treatment. Verified visually (Playwright,
  390×844) - the app remains fully usable, just with a persistent
  left-hand nav column.
- **Conversation list has no search/filter** - only pagination
  (`page`/`page_size`, reusing the existing shared convention). Adding
  search was not required by this phase's acceptance criteria.
- **No conversation rename/delete/archive** - explicitly out of scope
  (see the phase's own instructions); "New Chat" only ever navigates away,
  never deletes anything.
- **Title is derived once, from the first message, in English text as
  typed** - there is no i18n/localization concern in this project, and
  none was introduced.
