# NoteFlow Web MVP

This is the first minimal frontend for the upload and parse workflow.

It is intentionally static for now: no build step, no dependencies, and no framework migration cost before the backend workflow is stable.

## Run

From this folder:

```bash
python3 -m http.server 3000
```

Then open:

```text
http://localhost:3000
```

The page calls the API at:

```text
http://localhost:8080
```

To override the API base URL in the browser console:

```js
localStorage.setItem("noteflowApiBaseUrl", "http://localhost:8080");
```
