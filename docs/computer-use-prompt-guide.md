# Computer Use Prompt Guide

This app runs provider-native Computer Use. It can optionally add Web Search
and provider file retrieval. Prompts should describe the desktop outcome, the
allowed evidence, and the stop condition.

## Short Rule

Write the task like a work order for a careful desktop operator.

Include:

1. outcome
2. starting point
3. allowed sources
4. constraints
5. stop condition
6. final answer format
7. approval boundary

## Basic Template

```text
Outcome:
[What should be visible or completed.]

Starting point:
[App, website, file, or current screen.]

Sources:
[Visible page, attached files, official docs, Web Search, or a specific domain.]

Constraints:
[Things not to click, submit, buy, delete, publish, save, or change.]

Stop condition:
[The visible state that means the run is done.]

Final answer:
[What to report back.]

Approval boundary:
[Actions that require stopping and asking me first.]
```

## Tool-Aware Prompting

Web Search off:

```text
Use only the visible desktop and the page I open.
Do not browse elsewhere unless I ask.
```

Web Search on:

```text
Use Web Search only for current public information.
Prefer official sources.
Ignore unrelated search results.
```

Files uploaded:

```text
Use the attached file as the source of truth.
If the website disagrees with the file, report the mismatch before acting.
```

Files plus Web Search:

```text
Use the attached file for internal requirements.
Use Web Search only to verify current public facts.
Keep those two sources separate in the final answer.
```

Gemini with files:

```text
Do not upload reference files for Gemini Computer Use sessions.
Use OpenAI or Anthropic when the task needs file retrieval plus Computer Use.
```

## Good Examples

Find a page:

```text
Open the browser and go to the official Anthropic docs.
Find the Computer Use tool page.
Do not sign in or change settings.
Stop when the relevant docs page is visible.
Tell me the page title and the section heading.
```

Use a reference file:

```text
Use the attached onboarding checklist as the source of truth.
Open the internal dashboard already available in the browser.
Check whether the required fields are present.
Do not save, submit, or edit anything.
Stop after inspecting the form.
Return missing fields as a short bullet list.
```

Use live web context:

```text
Use Web Search and official sources only.
Find the current pricing page for the selected provider.
Open the page in the browser.
Stop when the pricing table is visible.
Summarize the relevant price and include the visible source page title.
```

Handle risk:

```text
Open the billing settings page.
Inspect the current plan and available upgrade options.
Do not click upgrade, confirm, purchase, or save.
If a confirmation dialog appears, stop and ask me.
Final answer: current plan, visible upgrade options, and any blocker.
```

## What To Avoid

Avoid vague prompts:

```text
Research this.
Fix the account.
Find what is wrong.
Do the thing in the file.
```

Avoid hidden assumptions:

```text
Use the best source.
Click the right button.
Finish when done.
```

Avoid scripting low-level actions unless necessary:

```text
Click at 420,300, then type...
```

The model should decide the UI path. You should define the goal and boundary.

## Stop Conditions

Good stop conditions are visible or evidence-backed:

- stop when the settings page is visible
- stop when the relevant table row is visible
- stop after the form is filled but before submitting
- stop when an error message explains the blocker
- stop after comparing the page to the attached file

Weak stop conditions are subjective:

- stop when it looks good
- stop when you are confident
- stop when complete

## Approval Boundaries

Use explicit approval language for consequential actions:

```text
Ask before submitting, purchasing, deleting, publishing, saving settings,
sending messages, downloading secrets, rotating keys, inviting users, or
changing billing.
```

## Quick Checklist

Before starting a run, ask:

- Did I say what outcome matters?
- Did I say what sources are allowed?
- Did I upload files only for OpenAI or Anthropic?
- Did I define when to stop?
- Did I name actions that require approval?
