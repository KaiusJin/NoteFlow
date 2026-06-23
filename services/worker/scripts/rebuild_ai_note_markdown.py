from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from noteflow_worker.db.repository import Repository
from noteflow_worker.pipelines.generate_notes import assemble_note_markdown, build_section_summary, sort_note_sections


def main() -> int:
    repository = Repository()
    note_ids = load_ready_note_ids(repository)
    rebuilt = 0
    for note in note_ids:
        sections = sort_note_sections(repository.load_ai_note_sections(note["id"]))
        if not sections:
            print(f"Skipped {note['title']} v{note['note_version']}: no saved sections")
            continue
        markdown = assemble_note_markdown(note["title"], sections)
        summary = build_section_summary(sections)
        update_note_markdown(repository, note["id"], markdown, summary)
        rebuilt += 1
        print(f"Rebuilt {note['title']} v{note['note_version']} with {len(sections)} sections")
    print(f"rebuilt={rebuilt}")
    return 0


def load_ready_note_ids(repository: Repository) -> list[dict]:
    with repository.connect() as conn:
        rows = conn.execute(
            """
            SELECT n.id, d.title, n.note_version
            FROM document_ai_notes n
            JOIN documents d ON d.id = n.document_id
            WHERE n.status = 'READY'
            ORDER BY d.title, n.note_version
            """
        ).fetchall()
    return [dict(row) for row in rows]


def update_note_markdown(repository: Repository, note_id: str, markdown: str, summary: str) -> None:
    with repository.connect() as conn:
        conn.execute(
            """
            UPDATE document_ai_notes
            SET markdown = %s,
                summary = %s,
                updated_at = NOW()
            WHERE id = %s
            """,
            (markdown, summary, note_id),
        )


if __name__ == "__main__":
    raise SystemExit(main())
