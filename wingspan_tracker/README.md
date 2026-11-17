# Wingspan Tracker v2.3 — Smart Recheck of Flagged Drafts

## Installation

1. Pause the photo queue, close Wingspan Tracker, and quit any opened review windows.
2. Back up your **entire working tracker folder**, especially `wingspan_scores.db` and `photo_imports/` (this directory contains the source photos of existing drafts).
3. Copy `wingspan_tracker.py`, `photo_import.py`, `ollama_reader.py`, and `requirements.txt` into the **existing** app folder. Do **not** copy an empty or replacement database.
4. Start Ollama with `qwen3-vl:4b` installed. Open Wingspan Tracker via its normal shortcut.
5. Go to **Photo Import**, then press **Recheck All Flagged (AI)** in the flagged-games section. It works on existing draft records and their saved photos, not the photo queue.

## Verification levels

* **Verified — review:** Re-read values are complete, the handwritten totals independently match the category sums, and no corrections are suggested.
* **Corrected — review:** Re-reading produces a complete, mathematically consistent set of scores with changed fields, or the 6-to-0 OCR correction is applied. The review window displays the changed fields. Verify these against the photo.
* **Needs correction:** Missing names or scores, uncertain recognition, conflicting totals, failed AI calls, or missing original images. Previous draft values are retained.

**Review and explicit approval are required for every AI-derived game.** Verification levels mean automated consistency, not certainty of transcription. The rechecker never inserts official games, changes player stats, or deletes drafts. Nectar stays excluded.

The rechecker uses the source photo saved with the draft. It reads each distinct photograph once per run and maps top/bottom draft positions to the corresponding reread game. It updates flagged drafts only; existing approved games are untouched. If Ollama fails, the draft stays flagged and its original extracted scores remain in place.

## Limitations

* The rechecker is an experimental consistency/AI-reread pass. It cannot infer reliably which of several mutually inconsistent digits is correct. It **does not** automatically approve games or repair handwritten totals by substituting calculated totals.
* Background work is sequential, but Ollama/AMD GPU failures can still occur. If the process itself crashes, reopen the application and run the recheck again.
* Photo files must remain in `photo_imports/` where the original queue copied them.
* If a draft has already been manually corrected, **review proposed AI changes carefully** before approval. Saving the database and photograph backup is important.

No database schema migration or new Python dependencies are required.
