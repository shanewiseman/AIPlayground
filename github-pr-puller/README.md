# GitHub PR Puller

`github-pr-puller.py` collects unresolved GitHub PR review threads, sends structured context to an OpenAI agent, and writes implementation-focused outputs.

## What It Does

- Pulls unresolved, non-outdated PR review threads from GitHub.
- Groups eligible comments by thread.
- Skips already-consumed comments using checkpoint markers.
- Pulls modified file contents from the PR for extra LLM context.
- Calls OpenAI via `openai-agents-python`.
- Writes:
  - A human-readable markdown report.
  - A YAML implementation handoff file for coding LLMs.
  - A prompt-debug JSON payload (unless disabled).

## Requirements

- Python 3.10+
- `openai-agents` and `pydantic`
- GitHub token with Pull Request read + write access
- OpenAI API key

Install dependencies:

```bash
pip install openai-agents pydantic
```

## Environment Variables

- `GITHUB_TOKEN`: GitHub personal access token.
- `OPENAI_API_KEY`: OpenAI API key used by the agents SDK.

## Usage

From repo root:

```bash
python3 github-pr-puller/github-pr-puller.py <pr_number> <owner/repo> [options]
```

Examples:

```bash
python3 github-pr-puller/github-pr-puller.py 123 openai/openai-agents-python
python3 github-pr-puller/github-pr-puller.py 123 shanewiseman/AIPlayground --model gpt-5-mini
python3 github-pr-puller/github-pr-puller.py 123 shanewiseman/AIPlayground --service-tier priority
python3 github-pr-puller/github-pr-puller.py 123 shanewiseman/AIPlayground --output-file report.md
```

## CLI Arguments

- `pr_number` (required): PR number (example: `347`).
- `repository` (required): repository in `owner/repo` format (example: `openai/openai-agents-python`).
- `--github-token`: overrides `GITHUB_TOKEN`.
- `--model`: model for the agent (default: `gpt-5-mini`).
- `--service-tier`: OpenAI service tier for all LLM submissions. Valid values: `standard`, `flex`, `priority` (default: `flex`).
- `--output-file`: base report filename override.
- `--max-comments`: max comments to include in LLM input (default: `600`).
- `--quiet`: disable progress logs.
- `--print-report`: print markdown report to stdout.
- `--no-prompt-debug`: disable writing prompt-debug JSON file.

## Output Files

By default, the base report filename is:

```text
pr-review-<owner>-<repo>-pr-<number>.md
```

The script also writes:

- `<report>.prompt-debug.json` (contains full prompt/payload unless disabled)
- `<report_stem>.llm-implementation.yaml`

To avoid clobbering previous runs, files are indexed automatically:

- `... .1.md`, `... .2.md`, etc.
- Matching index is applied across report/debug/implementation files.

## Checkpoint Behavior

After consuming a thread, the script posts a checkpoint reply comment in GitHub:

- Prefix: `[github-pr-puller checkpoint]`
- Includes `thread_id=<id>` and UTC timestamp.

On future runs:

- If no checkpoint exists in a thread: all thread comments are eligible.
- If checkpoint(s) exist: only comments after the latest checkpoint are eligible.
- Checkpoint comments themselves are excluded from processing.

## Notes

- Prompt-debug JSON can contain sensitive code/content. Treat it as sensitive.
- The script tracks and prints runtimes for:
  - GitHub logic
  - OpenAI LLM logic
  - Total runtime
