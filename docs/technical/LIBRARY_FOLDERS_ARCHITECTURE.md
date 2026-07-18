# Library Folders Architecture

## 1. Scope and product boundary

The Folders section is NoteFlow's durable file library for markdown notes. It
unifies four user-facing note sources behind one API:

- editable notes generated from READY AI notes;
- editable notes seeded from parsed PDF Markdown;
- imported `.md`, `.markdown`, and `.txt` files;
- blank notes created directly in the editor or library.

Folders are an organization layer only. Moving a note into a folder does not
change document parsing, retrieval chunks, embeddings, AI note generation, or
study artifacts. The PDF parser and AI-note pipeline remain the source of truth
for generated source material; the library stores editable copies that users can
rename, move, edit, export, and delete.

## 2. Runtime architecture

```text
Web Folders section
  -> GET /folders
  -> GET /notes
       -> LibraryService ensures missing RAW notes exist for current user's
          document_markdown_documents rows
       -> returns note summaries grouped by sourceKind
  -> user opens note
       -> GET /notes/{noteId}
       -> Editor autosaves with PUT /notes/{noteId}
```

Document-backed editor notes use the same `notes` table. Initializing an editor
note from a source writes a note with both `sourceDocumentId` and `sourceKind`.
The most recently updated document-backed note is the default document editor
note, while initialization only resets the matching source kind.

## 3. Data model

### folders

`folders` stores a per-user tree. `parentId = null` means a top-level folder.
Deleting a folder deletes its subtree and moves all contained notes to Unfiled.
Moving a folder rejects self/descendant cycles.

### notes

`notes` stores editable markdown bodies and their library metadata.

| Field | Purpose |
|---|---|
| `folderId` | Nullable folder assignment; `null` means Unfiled. |
| `sourceKind` | Normalized kind: `RAW`, `AI_NOTE`, `IMPORT`, or `BLANK`. |
| `sourceDocumentId` | Nullable source PDF document id for generated document-backed notes. |
| `markdown` | Editable markdown copy. List responses omit this large body. |

`sourceKind` is normalized on create and migration. Historical aliases such as
`PDF_MARKDOWN` and `RAW_MARKDOWN` are treated as `RAW`.

## 4. PDF Markdown library sync

Parsed PDF Markdown is produced by the worker in `document_markdown_documents`.
Those rows predate the library and are not themselves movable or editable. To
make the Folders view complete, `LibraryService.listNotes()` performs an
idempotent current-user sync:

1. Load the current user's documents.
2. Load matching `document_markdown_documents` rows.
3. For each document without a `notes(sourceDocumentId, sourceKind=RAW)` row,
   create a RAW note titled `{document.title} - PDF Markdown`.
4. Return the refreshed note summaries ordered by `updatedAt DESC`.

The sync only creates missing RAW notes. It does not overwrite an existing RAW
note's markdown, title, folder, or timestamps, because users may already have
edited or moved that copy.

## 5. API surface

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/folders` | List current user's folders. |
| `POST` | `/folders` | Create top-level folder or subfolder. |
| `PUT` | `/folders/{folderId}` | Rename or move a folder. |
| `DELETE` | `/folders/{folderId}` | Delete subtree and unfile contained notes. |
| `GET` | `/notes` | Sync missing RAW notes, then list note summaries. |
| `GET` | `/notes/{noteId}` | Load one editable note body. |
| `POST` | `/notes` | Create blank/import-style note with normalized source kind. |
| `PUT` | `/notes/{noteId}` | Rename, move, or update note markdown. |
| `DELETE` | `/notes/{noteId}` | Delete a library note. |
| `POST` | `/notes/import` | Import markdown/text file as `IMPORT`. |
| `GET` | `/notes/{noteId}/export` | Download note markdown. |

## 6. Web behavior

The Folders section keeps two independent navigation groups:

- smart views: All notes, AI notes, PDF markdown, Imported, My notes;
- folder tree: Unfiled and nested user folders.

Smart views filter by `sourceKind` and ignore folder assignment. Folder rows
filter by `folderId`. Dragging a note onto a folder calls `PUT /notes/{noteId}`
with `move=true`; the panel then switches to the destination folder.

## 7. Verification checklist

- `GET /notes` returns RAW summaries for every parsed PDF Markdown document
  owned by the current user.
- Selecting PDF markdown in the Folders view shows those RAW summaries.
- Opening a RAW note loads its markdown through `GET /notes/{noteId}`.
- Moving a RAW note changes only `folderId`; it does not rewrite
  `document_markdown_documents`.
- Repeated `GET /notes` calls do not create duplicate RAW notes.
- Legacy `document_editable_notes` rows migrate with normalized source kinds.
