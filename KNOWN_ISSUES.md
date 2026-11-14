# Known Issues

- Live Slack and GitHub webhooks do not ingest continuously; sources must be uploaded as text, CSV, or Markdown files.
- Vector database search and conversational chatbot interfaces are not implemented; retrieval is deterministic and structured.
- User accounts, passwords, and SSO are not implemented; role switching uses signed browser session cookies.
- Live model extraction fails when API credentials are absent; the application relies on the committed cache in `cache_only` mode.
- Evaluation trap TR01 (superseded decision) and TR03 (cross-label conflict) fail against the committed synthetic cache.
- Export to PDF, Word documents, or email is not implemented; handovers are viewed and acknowledged directly in the web UI.
- Dark mode is not implemented; only the light warm-paper palette is styled.
- Viewports narrower than 640px stack the source drawer over content rather than displaying side-by-side.
- The free hosted demo takes about a minute on cold start after idle, and all visitors share a single demo case (click 'Reset demo' if closed).
