# Gemini Successor Evaluation Checklist

Use this checklist when the Gemini lifecycle watchdog reports that `gemini-3-flash-preview` has a deprecation or shutdown announcement on the Gemini API changelog.

Successor discovery starts on the Gemini models overview page:

- Models overview: https://ai.google.dev/gemini-api/docs/models

The models overview is only for finding candidate replacements. Do not add a successor to the combined-tool allowlist until the candidate's individual model page shows all required capabilities explicitly.

## Required checks

1. Open the candidate's individual model page from the models overview.
2. Inspect the `Capabilities` row on that model page.
3. Confirm the page explicitly says `Computer use Supported`.
4. Confirm the page explicitly says `Search grounding Supported`.
5. Confirm the page explicitly says `Function calling Supported`.
6. If any one of those three labels is missing or marked unsupported, do not add the model to the Gemini combined-tool allowlist.

## Reference docs

- Gemini changelog: https://ai.google.dev/gemini-api/docs/changelog
- Gemini Computer Use guide: https://ai.google.dev/gemini-api/docs/computer-use
- Gemini Google Search grounding guide: https://ai.google.dev/gemini-api/docs/google-search
- Gemini Function calling guide: https://ai.google.dev/gemini-api/docs/function-calling
- Gemini tool-combination guide: https://ai.google.dev/gemini-api/docs/tool-combination

## Repo update steps after a candidate passes

1. Update the Gemini combined-tool allowlist in `backend/models/allowed_models.json`.
2. Update the Gemini adapter assumptions in `backend/engine/gemini.py` if the successor model id or capability notes changed.
3. Re-run the Gemini changelog watchdog and the Gemini adapter test slices before merging.

If no individual model page currently shows all three required labels, leave the allowlist unchanged and treat the combined-tool path as blocked until Google publishes a compatible successor.