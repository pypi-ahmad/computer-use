# Gemini Successor Evaluation Checklist

Use this checklist when an allowlisted Gemini Computer Use model
(`gemini-3.7-flash` or `gemini-3.5-flash-lite`) has a deprecation,
shutdown announcement, or documented successor. The purpose is to keep the
Gemini allowlist strictly tied to official capability documentation instead of
assuming that a newer Gemini model supports Computer Use.

Related documentation:

- [Computer Use Prompt Guide](computer-use-prompt-guide.md)
- [Technical Architecture](../TECHNICAL.md)
- [Operator Usage Guide](../USAGE.md)

Successor discovery starts on the Gemini models overview page:

- Models overview: https://ai.google.dev/gemini-api/docs/models

The models overview is only for finding candidate replacements. Do not add a
successor to the Gemini allowlist until the candidate's individual model page
shows the required capabilities explicitly.

## Required checks

1. Open the candidate's individual model page from the models overview.
2. Inspect the `Capabilities` row on that model page.
3. Confirm the page explicitly says `Computer use Supported`.
4. Confirm the Computer Use guide documents a compatible Interactions API
   request and continuation mechanism for the candidate.
5. Web-search planning in this repo uses Fetch MCP
   (`backend/infra/mcp_fetch.py`, `uvx mcp-server-fetch`), not Gemini
   Search grounding. Do **not** require `Search grounding Supported` to
   keep planning available. Confirm Computer Use still works without a
   search tool on the CU loop.
6. Confirm the Computer Use guide or model page does not exclude the candidate
   from Computer Use, prompt-injection detection, or its safety acknowledgement
   flow.
7. If any required label is missing or marked unsupported, do not add the model
   to the Gemini allowlist.

## File-search rule

Do not enable Gemini reference-file uploads as part of this checklist. This app
rejects Gemini files for Computer Use sessions unless Google publishes an
official Computer Use plus File Search combination and the provider adapter is
updated deliberately.

## Reference docs

- Gemini changelog: https://ai.google.dev/gemini-api/docs/changelog
- Gemini Computer Use guide: https://ai.google.dev/gemini-api/docs/computer-use
- Gemini Google Search grounding guide: https://ai.google.dev/gemini-api/docs/google-search
- Gemini Function calling guide: https://ai.google.dev/gemini-api/docs/function-calling
## Repo update steps after a candidate passes

1. Update the Gemini allowlist in `backend/models/allowed_models.json`.
2. Update the Gemini adapter assumptions in `backend/engine/gemini.py` if the successor model id or capability notes changed.
3. Re-run the Gemini changelog watchdog and the Gemini adapter test slices before merging.
4. Confirm Gemini file uploads still reject unless the file-search rule above
   has changed with official documentation.

If no individual model page documents all required Computer Use and
Interactions capabilities, leave the allowlist unchanged and treat the
successor path as blocked until Google publishes a compatible successor.

## Documentation updates after a change

If a successor passes the checks and is added to the repo, update every public
operator-facing reference in the same change:

- [README.md](../README.md) model matrix
- [USAGE.md](../USAGE.md) model selection table
- [TECHNICAL.md](../TECHNICAL.md) provider-adapter notes
- [Deployment guide](deployment.md) OAuth and route configuration
- [Computer Use Prompt Guide](computer-use-prompt-guide.md) provider guidance if
  the prompt shape, planning behavior, or file behavior changes
