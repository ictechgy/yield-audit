---
name: New transcript adapter
about: Request support for another AI coding agent's session logs
title: "Adapter: <vendor name>"
labels: adapter
---

**Which agent CLI?** (name + version)

**Where does it store session transcripts locally?**
(e.g. `~/.vendor/projects/<munged-cwd>/*.jsonl`)

**Schema grounding — required.** Paste a redacted sample record for each
record type (session metadata, a model call with token usage, a tool
call, a tool result/error). Strip secrets and file contents; keep key
names and nesting exactly as they appear.

```
<paste records here>
```

**Notes:** where token counts live, how errors are flagged, any layout
that would allow skipping old files (date directories, per-project
folders).
