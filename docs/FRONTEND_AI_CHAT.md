# CareSphere AI — Frontend AI Chat + Sources/Citations UX

Phase 16 turns the backend's secure, already-authorized `POST
/api/rag/answer` pipeline (Phases 9-15) into a usable conversational
product: a chat page where a signed-in user asks a question, sees a
grounded answer, and can inspect the exact backend-authorized evidence
behind it. **The frontend makes no authorization, retrieval, or clinical
decision of any kind.** It renders what the backend already decided.

## Frontend chat architecture

```
ChatPage (src/features/chat/pages/ChatPage.tsx)
  ├── ChatMessageItem (src/features/chat/components/ChatMessage.tsx)
  │     └── ReactMarkdown (citation-aware `a` component override)
  ├── SourcePanel (src/features/chat/components/SourcePanel.tsx)
  └── citations.ts (pure parsing/formatting helpers, no JSX)
        ↓
  lib/api.ts's askAi() - the existing, single API client module
        ↓
  POST /api/rag/answer (unmodified backend contract)
```

Reused, not rebuilt: the existing `AuthProvider`/`ProtectedRoute` (chat
sits behind the same route guard as every other page - no second auth
flow), the existing `lib/api.ts` module (`apiFetch`'s Supabase-token
attachment, unchanged), the existing plain-CSS design system
(`src/index.css` - no new UI framework, no CSS-in-JS, no component
library), and the existing `AppShell` sidebar (chat was already its
first/default nav item and route, `/chat`, from Phase 1's scaffold - this
phase filled in what was previously a placeholder page).

## API contract

Exactly the existing, unmodified backend contract - inspected directly
from `backend/app/schemas/rag.py` before writing any frontend code, never
assumed:

```http
POST /api/rag/answer
Authorization: Bearer <supabase access token>
Content-Type: application/json

{ "query": string, "top_k"?: number, "conversation_id"?: string | null }
```

```jsonc
{
  "answer": string,
  "sources": [{
    "number": number, "id": string, "type": SourceType, "label": string,
    "document_id": string | null, "document_title": string | null,
    "document_type": string | null, "chunk_id": string | null,
    "page": number | null, "section": string | null
  }],
  "model": string | null,
  "conversation_id": string | null,
  "request_id": string | null
}
```

`SourceType` is the backend's own closed enum (`backend/app/sources/models.py`):
`document`, `appointment`, `medical_record`, `lab_report`, `prescription`,
`doctor`, `department`, `administrative_summary`. The frontend's
`SourceType` union (`src/lib/api.ts`) mirrors it exactly - no `as any`,
no invented values.

**Nothing was added to the request beyond what the backend already
accepts.** `RagAnswerRequest`'s `extra="forbid"` means there is no field
for `hospital_id`/`role`/`permissions`/`model`/`system_prompt`/`sql`/
`admin_intent`/etc. to be sent even if the frontend tried - the boundary
is structural, not a frontend convention this code has to remember to
respect.

### One small, additive backend change

`app/main.py`'s `CORSMiddleware` gained `expose_headers=["X-Request-ID"]`.
Without it, a cross-origin browser (the frontend's dev server and the
backend run on different ports/origins) cannot read the `X-Request-ID`
response header via JavaScript at all, per the Fetch spec's default
CORS-safelisted-header behavior - the header is sent but invisible to
`response.headers.get(...)`. This is the *only* backend change Phase 16
required: it doesn't change what the header contains, who can set it, or
any authorization/security decision - it only makes an already-safe,
already-sent value readable by the same origin that was already permitted
to call the API. Covered by
`backend/tests/test_request_context.py::test_x_request_id_is_exposed_to_cross_origin_browser_javascript`.
No other backend endpoint, schema, or authorization path was touched.

## Chat implementation

- **Page** (`ChatPage.tsx`): owns all chat state (`messages`,
  `conversationId`, `input`, `isSending`, `selectedSourceNumber`,
  `lastQuery` for retry) and the submission flow. No Redux/React Query -
  this project has neither installed, and chat state is simple enough
  (a handful of `useState` calls) that introducing either would be an
  unjustified new dependency for this phase.
- **Messages**: a user message renders as plain, pre-wrapped text (React's
  default JSX text interpolation already escapes everything - no
  `dangerouslySetInnerHTML` anywhere in this codebase). An assistant
  message renders through `react-markdown` (added - see "Markdown
  rendering" below).
- **Input**: a `<textarea>` with an accessible `<label>` (not just a
  placeholder), `Enter` sends, `Shift+Enter` inserts a newline. The send
  button is disabled while a request is in flight or the input is blank,
  preventing duplicate submissions from a double click or
  Enter-then-click.
- **Loading**: a "CareSphere is thinking" indicator with a CSS-only pulse
  animation (respecting `prefers-reduced-motion`) - genuinely non-
  streaming, matching the real, non-streaming `/api/rag/answer` contract.
  No chunked/simulated typing of a completed answer.
- **Errors**: normalized through a new `AiRequestError` class
  (`src/lib/api.ts`) that maps HTTP status to one safe, fixed sentence
  (404 → "conversation no longer available", 401 → "sign in again", 422 →
  "enter a valid question", 5xx/network → generic retry message) plus an
  optional "Reference ID" line built from `X-Request-ID` - never a raw
  backend `detail` string, stack trace, or exception.
- **New chat**: clears `messages`/`conversationId`/`selectedSourceNumber`
  and focuses the input. It never calls the backend - there is no
  conversation-deletion endpoint, and none was added (out of scope, see
  below).

## Conversation handling

- **Conversation id**: entirely backend-owned. The frontend never
  generates one; it sends `conversation_id: null` on the first turn,
  reads the backend-assigned id off the response, and echoes it back on
  every subsequent turn in the same browser session.
- **Continuation**: works exactly as the backend already implements it
  (Phase 13) - each turn independently re-authorizes; the frontend does
  nothing special to make follow-ups work beyond passing the same id back.
- **Refresh / conversation history availability - a real limitation, not
  papered over**: there is no `GET` endpoint to list conversations or
  reload a conversation's past messages (inspected `backend/app/api/rag.py`
  and `conversation_repository.py` directly - confirmed none exists, and
  none was added). A page reload starts a brand-new conversation; the
  previous one still exists in the database and remains resumable in
  principle, but this UI has no way to look it up again. Building a
  bounded, ownership-checked `GET /api/conversations/{id}/messages`
  endpoint was considered and deliberately **not** built in this phase -
  it is real, additional backend scope, not a "genuinely missing
  capability the chat UI cannot work without" (the chat UI works
  correctly for a single browser session without it). Documented here as
  a known limitation and future-work item, per this phase's own
  instruction to avoid expanding backend scope unnecessarily.

## Citation implementation

`src/features/chat/citations.ts` (pure functions, no rendering):

1. `linkifyCitations(text)` rewrites every `[Source N]` marker (mirroring
   `backend/app/sources/citations.py`'s exact regex,
   `\[Source\s+(-?\d+)\]`, case-insensitive) into a markdown link
   `[Source N](#source-N)` - reusing `react-markdown`'s own link-element
   extension point rather than writing a custom AST plugin or a second
   markdown parser.
2. `ChatMessageItem` overrides `react-markdown`'s `a` component: if the
   href matches `#source-N` **and** `sources` (from the same response)
   contains a source numbered `N`, it renders an interactive citation
   button wired to open the source panel. If the number doesn't match any
   real source (a hallucinated `[Source 999]`), it renders the same text
   as an inert, non-interactive `<span>` - **no source is ever invented
   from the number alone.** A genuine external link (a real `http(s)`
   URL) still renders as a normal anchor; anything else (`javascript:`,
   `data:`, a bare relative path) renders as plain text.
3. Citation authority is never re-derived or re-validated here -
   `backend/app/sources/citations.py`'s `validate_citations` already
   decided which markers are "real" before this code ever runs; this
   layer is presentation only.
4. **Source order and numbering are never touched.** `sources` is
   rendered and matched in exactly the order and numbering the backend
   returned - no frontend sort, no renumbering.

## Source UI

`SourcePanel.tsx` shows only fields the backend actually populated on
that specific `SourceReference`:

- **`document`**: title, document type (humanized from the backend's
  `HOSPITAL_POLICY`-style enum value), page, section - never raw chunk
  content (not part of this schema at all) and never a raw `document_id`/
  `chunk_id`.
- **Every structured type** (`appointment`, `medical_record`,
  `lab_report`, `prescription`, `doctor`, `department`) and
  **`administrative_summary`** (Admin AI, Phase 14): the backend's own
  safe `label` string (e.g. `"Appointment — 2026-10-02 09:30:00"`,
  `"Employee Summary — Total: 15"`) plus a human-readable type badge - no
  frontend reconstruction of a label from an id, because there is no id
  to reconstruct from (these source types carry no document-only fields
  at all, by the backend's own schema).

The same chat UI renders Admin AI answers with zero special-casing -
`administrative_summary` is just one more entry in the source-type-label
map. There is no separate "Admin AI mode" in the frontend; the backend
alone decides whether an `ADMIN_*` intent was authorized for this caller.

## Security

- **No frontend authorization bypass**: every question goes through the
  unmodified, authenticated `POST /api/rag/answer`; the frontend holds no
  role/permission/hospital logic beyond the pre-existing, already-
  documented UX-only nav-hiding (`auth/permissions.ts`, unchanged).
- **No direct Ollama access, no SQL, no frontend RAG/embeddings** - the
  browser only ever calls the one existing backend endpoint.
- **No service-role key or backend-only secret** anywhere in
  `frontend/` - verified by inspection of `.env.example`/`src/lib/supabase.ts`
  (only the public anon key) and by grepping the built bundle's source
  for `SUPABASE_SERVICE_ROLE`/`service_role` (no match).
- **No sensitive browser persistence** - grepped the entire `src/`
  directory for `localStorage`/`sessionStorage`/`dangerouslySetInnerHTML`:
  zero matches outside test files (and test files don't add any either -
  see `ChatPage.test.tsx`'s explicit storage-emptiness assertion).
  Conversation messages, the JWT, and RAG content live only in
  `AuthProvider`'s Supabase client (unchanged, pre-existing) and this
  page's own React state - never written to any browser storage or the
  URL.
- **Safe Markdown rendering** - `react-markdown` never interprets raw
  HTML in its input as real elements (no `rehype-raw`/`rehype-sanitize`
  plugin is used); a literal `<script>`/`<img onerror=...>` in model
  output, a source label, or a user's own message renders as inert text,
  confirmed by `ChatMessage.test.tsx`'s XSS-sentinel tests (checking both
  that no `<script>`/`<img>` element is ever created in the DOM and that
  no injected handler ever executes).
- **Safe source handling** - a source click never triggers an independent
  fetch; the panel renders only data already present in the response that
  was already authorized before the frontend ever saw it.
- **Prompt injection and historical-message trust** - the frontend does
  not parse, filter, or "detect" injection attempts of any kind; that
  protection is entirely backend-owned (Phases 10/13) and unchanged.
  Previous conversation turns are treated as plain display data, never as
  instructions to the frontend.

## Accessibility

- Every citation and source-chip button has a descriptive
  `aria-label` (`"View Source 1"`, not a generic "click here").
- The chat input has a real `<label>` (visually hidden via a standard
  `.sr-only` utility class, not solely a `placeholder`).
- Only the most recently added message is wrapped in `aria-live="polite"`
  - the entire message list is never one aggressive live region that
    re-announces the whole conversation on every update.
- Failed-message bubbles use `role="alert"`.
- Errors are never communicated by color alone (they carry text content
  in every case).
- The typing indicator respects `prefers-reduced-motion: reduce`.

## Browser storage policy

Nothing chat-related is ever written to `localStorage`, `sessionStorage`,
`IndexedDB`, or a URL query parameter - confirmed both by code inspection
(no API for any of these is called anywhere in `src/features/chat/` or
`src/lib/api.ts`'s new code) and by an automated test
(`ChatPage.test.tsx`) that runs a full send/receive cycle and then
asserts `localStorage.length === 0`/`sessionStorage.length === 0`. All
active chat state lives in React component state only, and disappears
on reload by design (see "Conversation handling").

## Responsive behavior

A single CSS breakpoint (`max-width: 800px`) stacks the source panel
below the chat panel instead of beside it and lets message bubbles use a
wider share of the viewport. Verified visually (Playwright, real browser,
real CSS - see the Phase 16 report's testing section) at 1280×800
(desktop) and 390×844 (mobile) viewports, plus a `prefers-color-scheme:
dark` render - the existing app never introduced a dedicated theme
toggle; every color in this phase's CSS uses `currentColor`/`color-mix`
exactly like the rest of `index.css`, so it inherits the browser's
`color-scheme: light dark` behavior automatically, with no
chat-specific dark-mode code required.

## Known limitations

- **No conversation history/list UI** - see "Conversation handling."
  A refreshed page cannot resume a previous conversation's visible
  messages (though the backend conversation itself is untouched and
  still exists).
- **No streaming** - deliberately; the backend endpoint is synchronous,
  and this phase does not simulate streaming by chunking a completed
  answer, per its own explicit instruction.
- **Citation text adjacent to real markdown link syntax** is a narrow,
  documented edge case: `linkifyCitations` operates on the raw string
  before markdown parsing, so a citation marker immediately followed by
  `(...)` in the *same* answer (only reachable via adversarial injected
  document content, not normal usage) could produce a malformed link
  rather than a clean citation. This is a cosmetic rendering glitch, not
  a security issue - `react-markdown`'s no-raw-HTML default means no
  script ever executes regardless of how the text is mangled.
- **No conversation-level regenerate/feedback/copy actions** - out of
  scope for this phase (see below), consistent with the source material's
  own instruction to keep scope focused.
- **No environment file exists in this development environment** for a
  real Supabase project (`frontend/.env` was never created here), so a
  fully authenticated, real-Supabase, real-Ollama browser walkthrough of
  the golden path could not be captured in this session. Verified
  instead via: (a) 29 automated frontend tests exercising the identical
  code paths with a mocked API layer, (b) a real browser (Playwright)
  against the real Vite dev server with the real `POST /api/rag/answer`
  network call intercepted and fulfilled with a realistic, schema-
  accurate response, confirming the actual bundled UI code (markdown,
  citations, source panel, responsive layout, dark mode) renders
  correctly end-to-end, and (c) a real, unmocked request against the real
  running backend that correctly produced and displayed a safe 401 error
  (proving the real network path, real CORS `expose_headers` config, and
  real error-handling code all work together).

## Out of scope

Confirmed not built: streaming/SSE, WebSockets, frontend RAG/embeddings/
vector search, frontend SQL generation or execution, direct Ollama calls
from the browser, AI answer quality scoring, an audit-log dashboard,
autonomous actions of any kind, conversation regeneration, and message
feedback (thumbs up/down).
