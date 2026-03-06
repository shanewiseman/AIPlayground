#!/usr/bin/env python3
"""Pull unresolved GitHub PR review comments and prepare implementation guidance."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import textwrap
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"
GITHUB_API_VERSION = "2022-11-28"
DEFAULT_MODEL = "gpt-5"

THREADS_QUERY = """
query PullRequestThreads(
  $owner: String!
  $name: String!
  $prNumber: Int!
  $threadsCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $prNumber) {
      number
      title
      url
      reviewThreads(first: 50, after: $threadsCursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          path
          comments(first: 100) {
            pageInfo {
              hasNextPage
              endCursor
            }
            nodes {
              id
              databaseId
              url
              body
              createdAt
              path
              diffHunk
              line
              startLine
              originalLine
              originalStartLine
              outdated
              author {
                login
              }
            }
          }
        }
      }
    }
  }
}
"""

THREAD_COMMENTS_QUERY = """
query ReviewThreadComments($threadId: ID!, $commentsCursor: String) {
  node(id: $threadId) {
    ... on PullRequestReviewThread {
      comments(first: 100, after: $commentsCursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          databaseId
          url
          body
          createdAt
          path
          diffHunk
          line
          startLine
          originalLine
          originalStartLine
          outdated
          author {
            login
          }
        }
      }
    }
  }
}
"""

PR_FILES_QUERY = """
query PullRequestFiles(
  $owner: String!
  $name: String!
  $prNumber: Int!
  $filesCursor: String
) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $prNumber) {
      headRefOid
      files(first: 100, after: $filesCursor) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          path
        }
      }
    }
  }
}
"""

FILE_CONTENT_QUERY = """
query FileContent($owner: String!, $name: String!, $expression: String!) {
  repository(owner: $owner, name: $name) {
    object(expression: $expression) {
      ... on Blob {
        text
        isBinary
        byteSize
      }
    }
  }
}
"""

MULTI_SPACE_OR_TAB_RE = re.compile(r"[ \t]+")
MULTI_NEWLINE_RE = re.compile(r"\n+")


class ProgressReporter:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled

    def log(self, message: str) -> None:
        if not self.enabled:
            return
        timestamp = time.strftime("%H:%M:%S", time.localtime())
        print(f"[{timestamp}] {message}", file=sys.stderr, flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch unresolved GitHub pull request review comments, send them to an "
            "OpenAI agent for analysis, and print a copy/paste-friendly report."
        ),
        formatter_class=argparse.RawTextHelpFormatter,
        epilog=textwrap.dedent(
            """\
            Environment variables:
              GITHUB_TOKEN    GitHub personal access token with repo read access.
              OPENAI_API_KEY  OpenAI API key used by openai-agents-python.

            Examples:
              python github-pr-puller.py 123 owner/repo
              python github-pr-puller.py 123 owner/repo --model gpt-5-mini
              python github-pr-puller.py 123 owner/repo --output-file report.md
            """
        ),
    )
    parser.add_argument(
        "pr_number",
        type=int,
        help="Pull request number (integer), for example: 347",
    )
    parser.add_argument(
        "repository",
        help="Repository in 'owner/repo' format, for example: openai/openai-agents-python",
    )
    parser.add_argument(
        "--github-token",
        default=os.environ.get("GITHUB_TOKEN"),
        help="GitHub token (defaults to GITHUB_TOKEN env var).",
    )
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Model for openai-agents-python (default: {DEFAULT_MODEL}).",
    )
    parser.add_argument(
        "--output-file",
        help=(
            "Override output markdown path. By default, the script saves automatically "
            "using the repo and PR number in the filename."
        ),
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=600,
        help="Maximum unresolved comments to include in LLM input (default: 600).",
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Disable progress logs (logs are printed to stderr by default).",
    )
    return parser.parse_args()


def parse_repository(repository: str) -> tuple[str, str]:
    if "/" not in repository:
        raise ValueError("Repository must be in 'owner/repo' format.")
    owner, name = repository.split("/", 1)
    if not owner or not name:
        raise ValueError("Repository must be in 'owner/repo' format.")
    return owner, name


def build_default_output_filename(owner: str, repo: str, pr_number: int) -> str:
    safe_owner = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in owner)
    safe_repo = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in repo)
    return f"pr-review-{safe_owner}-{safe_repo}-pr-{pr_number}.md"


def build_prompt_debug_filename(output_file: str) -> str:
    output_path = Path(output_file)
    return str(output_path.with_name(f"{output_path.name}.prompt-debug.json"))


def build_llm_implementation_filename(output_file: str) -> str:
    output_path = Path(output_file)
    return str(output_path.with_name(f"{output_path.name}.llm-implementation.yaml"))


def github_graphql(
    token: str,
    query: str,
    variables: dict[str, Any],
    *,
    operation_name: str,
    progress: ProgressReporter | None = None,
) -> dict[str, Any]:
    request_started_at = time.monotonic()
    if progress:
        progress.log(f"GitHub API request started: {operation_name}")
    payload = json.dumps({"query": query, "variables": variables}).encode("utf-8")
    req = urllib.request.Request(
        url=GITHUB_GRAPHQL_URL,
        data=payload,
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "github-pr-puller-script",
            "X-GitHub-Api-Version": GITHUB_API_VERSION,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            raw = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        if progress:
            progress.log(f"GitHub API request failed ({operation_name}): HTTP {exc.code}")
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        if progress:
            progress.log(f"GitHub API request failed ({operation_name}): network error")
        raise RuntimeError(f"GitHub API request failed: {exc}") from exc

    result = json.loads(raw)
    if "errors" in result and result["errors"]:
        if progress:
            progress.log(f"GitHub API request failed ({operation_name}): GraphQL errors returned")
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    if progress:
        duration = time.monotonic() - request_started_at
        progress.log(f"GitHub API request completed: {operation_name} ({duration:.2f}s)")
    return result.get("data", {})


def fetch_all_thread_comments(
    token: str,
    thread_id: str,
    first_page: dict[str, Any],
    progress: ProgressReporter,
) -> list[dict[str, Any]]:
    comments = list(first_page.get("nodes", []))
    page_info = first_page.get("pageInfo") or {}
    cursor = page_info.get("endCursor")
    has_next = bool(page_info.get("hasNextPage"))
    page_number = 1

    while has_next:
        page_number += 1
        data = github_graphql(
            token=token,
            query=THREAD_COMMENTS_QUERY,
            variables={"threadId": thread_id, "commentsCursor": cursor},
            operation_name=f"ReviewThreadComments thread={thread_id} page={page_number}",
            progress=progress,
        )
        node = data.get("node") or {}
        page = node.get("comments") or {}
        comments.extend(page.get("nodes", []))
        page_info = page.get("pageInfo") or {}
        cursor = page_info.get("endCursor")
        has_next = bool(page_info.get("hasNextPage"))

    return comments


def normalize_comment(thread: dict[str, Any], comment: dict[str, Any]) -> dict[str, Any]:
    comment_id = comment.get("id")
    file_path = comment.get("path") or thread.get("path") or ""
    return {
        "thread_id": thread.get("id"),
        "thread_path": thread.get("path"),
        "thread_outdated": bool(thread.get("isOutdated")),
        "comment_id": str(comment_id) if comment_id is not None else "",
        "comment_database_id": comment.get("databaseId"),
        "comment_url": comment.get("url"),
        "author": (comment.get("author") or {}).get("login"),
        "created_at": comment.get("createdAt"),
        "body": comment.get("body", ""),
        "file_path": str(file_path),
        "line": comment.get("line"),
        "start_line": comment.get("startLine"),
        "original_line": comment.get("originalLine"),
        "original_start_line": comment.get("originalStartLine"),
        "outdated": bool(comment.get("outdated")),
        "code_snippet": comment.get("diffHunk", ""),
    }


def fetch_unresolved_pr_comments(
    token: str,
    owner: str,
    repo: str,
    pr_number: int,
    progress: ProgressReporter,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    threads_cursor: str | None = None
    unresolved_comments: list[dict[str, Any]] = []
    pr_info: dict[str, Any] | None = None
    threads_page_number = 1

    progress.log(f"Loading unresolved PR comments for {owner}/{repo}#{pr_number}")

    while True:
        data = github_graphql(
            token=token,
            query=THREADS_QUERY,
            variables={
                "owner": owner,
                "name": repo,
                "prNumber": pr_number,
                "threadsCursor": threads_cursor,
            },
            operation_name=f"PullRequestThreads page={threads_page_number}",
            progress=progress,
        )
        repository_node = data.get("repository")
        if not repository_node:
            raise RuntimeError(f"Repository '{owner}/{repo}' was not found or is inaccessible.")

        pr_node = repository_node.get("pullRequest")
        if not pr_node:
            raise RuntimeError(f"Pull request #{pr_number} was not found in '{owner}/{repo}'.")

        if pr_info is None:
            pr_info = {
                "number": pr_node.get("number"),
                "title": pr_node.get("title"),
                "url": pr_node.get("url"),
                "repository": f"{owner}/{repo}",
            }

        review_threads = pr_node.get("reviewThreads") or {}
        thread_nodes = review_threads.get("nodes", [])
        active_thread_nodes = [
            thread
            for thread in thread_nodes
            if not thread.get("isResolved") and not thread.get("isOutdated")
        ]
        progress.log(
            "GitHub thread page "
            f"{threads_page_number}: total_threads={len(thread_nodes)}, "
            f"active_threads={len(active_thread_nodes)}"
        )

        for thread in active_thread_nodes:
            thread_id = str(thread.get("id") or "unknown")
            progress.log(
                f"Collecting comments from unresolved thread {thread_id} on "
                f"{thread.get('path') or 'unknown file'}"
            )
            first_page = thread.get("comments") or {}
            comments = fetch_all_thread_comments(
                token=token,
                thread_id=thread_id,
                first_page=first_page,
                progress=progress,
            )
            kept_comments = 0
            for comment in comments:
                if comment.get("outdated"):
                    continue
                unresolved_comments.append(normalize_comment(thread, comment))
                kept_comments += 1
            progress.log(
                f"Thread {thread_id}: collected {kept_comments}/{len(comments)} non-outdated comments "
                f"(running_total={len(unresolved_comments)})"
            )

        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        threads_cursor = page_info.get("endCursor")
        threads_page_number += 1

    progress.log(f"Finished GitHub collection: total_unresolved_comments={len(unresolved_comments)}")
    return pr_info or {}, unresolved_comments


def fetch_pr_modified_files_with_content(
    token: str,
    owner: str,
    repo: str,
    pr_number: int,
    progress: ProgressReporter,
) -> list[dict[str, str]]:
    files_cursor: str | None = None
    files_page_number = 1
    head_ref_oid: str | None = None
    modified_paths: set[str] = set()

    progress.log(f"Loading modified file paths for {owner}/{repo}#{pr_number}")
    while True:
        data = github_graphql(
            token=token,
            query=PR_FILES_QUERY,
            variables={
                "owner": owner,
                "name": repo,
                "prNumber": pr_number,
                "filesCursor": files_cursor,
            },
            operation_name=f"PullRequestFiles page={files_page_number}",
            progress=progress,
        )
        repository_node = data.get("repository")
        if not repository_node:
            raise RuntimeError(f"Repository '{owner}/{repo}' was not found or is inaccessible.")

        pr_node = repository_node.get("pullRequest")
        if not pr_node:
            raise RuntimeError(f"Pull request #{pr_number} was not found in '{owner}/{repo}'.")

        if head_ref_oid is None:
            oid_value = pr_node.get("headRefOid")
            if isinstance(oid_value, str) and oid_value:
                head_ref_oid = oid_value

        files_node = pr_node.get("files") or {}
        page_paths = 0
        for file_node in files_node.get("nodes", []):
            path = file_node.get("path")
            if isinstance(path, str) and path:
                modified_paths.add(path)
                page_paths += 1

        progress.log(
            f"GitHub file page {files_page_number}: fetched={page_paths}, "
            f"running_unique_files={len(modified_paths)}"
        )

        page_info = files_node.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        files_cursor = page_info.get("endCursor")
        files_page_number += 1

    if not head_ref_oid:
        raise RuntimeError("Unable to determine PR head commit SHA for file content retrieval.")

    file_contexts: list[dict[str, str]] = []
    ordered_paths = sorted(modified_paths)
    for idx, path in enumerate(ordered_paths, start=1):
        progress.log(f"Fetching file content {idx}/{len(ordered_paths)}: {path}")
        expression = f"{head_ref_oid}:{path}"
        content_data = github_graphql(
            token=token,
            query=FILE_CONTENT_QUERY,
            variables={"owner": owner, "name": repo, "expression": expression},
            operation_name=f"PullRequestFileContent {path}",
            progress=progress,
        )
        repository_node = content_data.get("repository") or {}
        object_node = repository_node.get("object") or {}

        if object_node.get("isBinary"):
            byte_size = object_node.get("byteSize")
            file_text = f"[binary file omitted from text context; byte_size={byte_size}]"
        else:
            file_text = str(object_node.get("text") or "")

        if not file_text:
            file_text = "[file content unavailable]"

        file_contexts.append({"path": path, "content": file_text})

    progress.log(f"Finished file content collection: total_unique_files={len(file_contexts)}")
    return file_contexts


def build_llm_payload(
    comments: list[dict[str, Any]],
    modified_files_with_content: list[dict[str, str]],
) -> dict[str, Any]:
    def normalize_modified_file_content(text: str) -> str:
        normalized = text.replace("\r\n", "\n").replace("\r", "\n")
        normalized = MULTI_SPACE_OR_TAB_RE.sub(" ", normalized)
        normalized = MULTI_NEWLINE_RE.sub("\n", normalized)
        return normalized

    return {
        "review_comments": [
            {
                "file_path": str(comment.get("file_path") or ""),
                "body": str(comment.get("body") or ""),
                "code_snippet": str(comment.get("code_snippet") or ""),
            }
            for comment in comments
        ],
        "modified_files": [
            {
                "path": str(file_obj.get("path") or ""),
                "content": normalize_modified_file_content(str(file_obj.get("content") or "")),
            }
            for file_obj in modified_files_with_content
        ],
    }


def build_analysis_prompt(payload: dict[str, Any]) -> str:
    return (
        "Analyze the following unresolved GitHub PR comments. "
        "Use both the review comments and the full content of each modified file for context. "
        "Each review comment item contains 'file_path', 'body', and 'code_snippet'. "
        "Each modified file item contains 'path' and full 'content'. "
        "Use the file contents actively when creating guidance.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )


def maybe_load_agents_locally() -> None:
    """Allow running from this repo without pip installing openai-agents."""
    if "agents" in sys.modules:
        return
    local_src = Path(__file__).resolve().parents[1] / "openai-agents-python" / "src"
    if local_src.exists():
        sys.path.insert(0, str(local_src))


def analyze_with_openai_agents(
    model: str,
    prompt: str,
    comment_count: int,
    progress: ProgressReporter,
) -> Any:
    maybe_load_agents_locally()
    try:
        from pydantic import BaseModel, Field
        from agents import Agent, Runner, set_tracing_disabled
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install openai-agents pydantic"
        ) from exc

    class CommentGuidance(BaseModel):
        requested_change_summary: str = Field(
            description="Short summary of what reviewer is asking to change."
        )
        technical_explanation: str = Field(
            description="Technical interpretation of the comment and expected code behavior."
        )
        implementation_prompt: str = Field(
            description="Direct implementation prompt suitable for another coding LLM."
        )

    class PRGuidance(BaseModel):
        overall_summary: str = Field(
            description="Overall summary of requested PR changes across all unresolved comments."
        )
        implementation_strategy: str = Field(
            description="Suggested plan/order to implement requested changes."
        )
        comments: list[CommentGuidance] = Field(
            description="One guidance object per unresolved comment."
        )

    agent = Agent(
        name="GitHub PR Review Analyst",
        model=model,
        instructions=textwrap.dedent(
            """
            You analyze unresolved GitHub pull request review comments.
            Return concise but technically precise output for an implementation LLM.
            Requirements:
            1. Produce one comment entry per input unresolved comment.
            2. Use the provided modified file contents to add implementation context.
            3. Keep guidance implementation-focused and avoid generic advice.
            4. If a comment is ambiguous, state assumptions explicitly in technical_explanation.
            """
        ).strip(),
        output_type=PRGuidance,
    )

    set_tracing_disabled(True)
    progress.log(f"LLM call 1 started (model={model}, unresolved_comments={comment_count})")
    llm_started_at = time.monotonic()
    result = Runner.run_sync(agent, prompt)
    duration = time.monotonic() - llm_started_at
    progress.log(f"LLM call 1 completed in {duration:.2f}s")
    return result.final_output


def block(title: str, text: str) -> str:
    clean = (text or "").strip()
    if not clean:
        clean = "(empty)"
    return (
        f"## {title}\n\n"
        "```text\n"
        f"{clean}\n"
        "```\n"
    )


def render_report(analysis: Any, pr_info: dict[str, Any], comments_count: int) -> str:
    header = textwrap.dedent(
        f"""\
        # PR Review Synthesis

        Repository: `{pr_info.get("repository")}`
        Pull Request: `#{pr_info.get("number")}` - {pr_info.get("title")}
        PR URL: {pr_info.get("url")}
        Unresolved comments analyzed: {comments_count}
        """
    ).strip()

    sections: list[str] = [header]
    sections.append(block("Overall Summary", analysis.overall_summary))
    sections.append(block("Implementation Strategy", analysis.implementation_strategy))

    for idx, comment in enumerate(analysis.comments, start=1):
        sections.append(
            textwrap.dedent(
                f"""\
                ## Comment {idx}
                """
            ).strip()
        )
        sections.append(block("Requested Change Summary", comment.requested_change_summary))
        sections.append(block("Technical Explanation", comment.technical_explanation))
        sections.append(block("Implementation Prompt", comment.implementation_prompt))

    return "\n\n".join(sections).rstrip() + "\n"


def _yaml_block(text: str, indent: int = 2) -> str:
    prefix = " " * indent
    clean = (text or "").rstrip()
    if not clean:
        return f"|-\n{prefix}(empty)"
    indented_lines = "\n".join(f"{prefix}{line}" for line in clean.splitlines())
    return f"|-\n{indented_lines}"


def _yaml_scalar(value: Any) -> str:
    if value is None:
        return "\"\""
    text = str(value)
    escaped = text.replace("\\", "\\\\").replace("\"", "\\\"")
    return f"\"{escaped}\""


def render_llm_implementation_file(analysis: Any, pr_info: dict[str, Any]) -> str:
    lines: list[str] = [
        "intent: |-",
        "  This file is meant for an implementation-focused LLM.",
        "  Implement each implementation_prompt using requested_change_summary and",
        "  technical_explanation as supporting context.",
        f"repository: {_yaml_scalar(pr_info.get('repository'))}",
        f"pull_request_number: {_yaml_scalar(pr_info.get('number'))}",
        f"pull_request_title: {_yaml_scalar(pr_info.get('title'))}",
        f"pull_request_url: {_yaml_scalar(pr_info.get('url'))}",
        f"overall_summary_context: {_yaml_block(str(analysis.overall_summary), indent=2)}",
        f"implementation_strategy_context: {_yaml_block(str(analysis.implementation_strategy), indent=2)}",
        "implementation_items:",
    ]

    for idx, comment in enumerate(analysis.comments, start=1):
        lines.extend(
            [
                f"  - item_number: {idx}",
                f"    requested_change_summary_context: {_yaml_block(str(comment.requested_change_summary), indent=6)}",
                f"    technical_explanation_context: {_yaml_block(str(comment.technical_explanation), indent=6)}",
                f"    implementation_prompt_primary_instruction: {_yaml_block(str(comment.implementation_prompt), indent=6)}",
            ]
        )

    if not analysis.comments:
        lines.append("  []")

    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    run_started_at = time.monotonic()
    github_elapsed_seconds = 0.0
    openai_elapsed_seconds = 0.0
    args = parse_args()
    progress = ProgressReporter(enabled=not args.quiet)
    progress.log("Starting PR review synthesis run")

    try:
        owner, repo = parse_repository(args.repository)
    except ValueError as exc:
        raise SystemExit(str(exc))
    output_file = args.output_file or build_default_output_filename(owner, repo, args.pr_number)

    if not args.github_token:
        raise SystemExit(
            "Missing GitHub token. Set GITHUB_TOKEN or provide --github-token."
        )
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Missing OPENAI_API_KEY environment variable.")

    github_started_at = time.monotonic()
    pr_info, comments = fetch_unresolved_pr_comments(
        token=args.github_token,
        owner=owner,
        repo=repo,
        pr_number=args.pr_number,
        progress=progress,
    )

    if not comments:
        github_elapsed_seconds = time.monotonic() - github_started_at
        progress.log(f"GitHub logic completed in {github_elapsed_seconds:.2f}s")
        print(
            textwrap.dedent(
                f"""\
                No unresolved pull request review comments were found.
                Repository: {owner}/{repo}
                Pull Request: #{args.pr_number}
                """
            ).strip()
        )
        total_elapsed_seconds = time.monotonic() - run_started_at
        print(f"GitHub logic runtime (seconds): {github_elapsed_seconds:.2f}")
        print(f"OpenAI LLM runtime (seconds): {openai_elapsed_seconds:.2f}")
        print(f"Total runtime (seconds): {total_elapsed_seconds:.2f}")
        return

    comments = comments[: args.max_comments]
    progress.log(
        f"Collecting modified file contents for additional context (comments={len(comments)})"
    )
    modified_files_with_content = fetch_pr_modified_files_with_content(
        token=args.github_token,
        owner=owner,
        repo=repo,
        pr_number=args.pr_number,
        progress=progress,
    )
    github_elapsed_seconds = time.monotonic() - github_started_at
    progress.log(f"GitHub logic completed in {github_elapsed_seconds:.2f}s")

    progress.log(
        f"Preparing LLM input payload with {len(comments)} comments and "
        f"{len(modified_files_with_content)} modified files "
        f"(max_comments={args.max_comments})"
    )
    payload = build_llm_payload(
        comments=comments,
        modified_files_with_content=modified_files_with_content,
    )
    prompt = build_analysis_prompt(payload)
    prompt_debug_file = build_prompt_debug_filename(output_file)
    llm_implementation_file = build_llm_implementation_filename(output_file)
    prompt_debug_doc = {
        "model": args.model,
        "prompt_payload": payload,
        "prompt_text": prompt,
    }
    Path(prompt_debug_file).write_text(json.dumps(prompt_debug_doc, indent=2), encoding="utf-8")
    progress.log(f"Saved prompt debug payload to: {prompt_debug_file}")

    openai_started_at = time.monotonic()
    analysis = analyze_with_openai_agents(
        model=args.model,
        prompt=prompt,
        comment_count=len(comments),
        progress=progress,
    )
    openai_elapsed_seconds = time.monotonic() - openai_started_at
    progress.log(f"OpenAI LLM logic completed in {openai_elapsed_seconds:.2f}s")
    progress.log("Rendering final output report")
    report = render_report(analysis=analysis, pr_info=pr_info, comments_count=len(comments))
    llm_impl_report = render_llm_implementation_file(analysis=analysis, pr_info=pr_info)

    #print(report)
    Path(output_file).write_text(report, encoding="utf-8")
    Path(llm_implementation_file).write_text(llm_impl_report, encoding="utf-8")
    print(f"Saved report to: {output_file}")
    print(f"Saved LLM implementation file to: {llm_implementation_file}")
    print(f"Saved prompt debug payload to: {prompt_debug_file}")
    total_elapsed_seconds = time.monotonic() - run_started_at
    print(f"GitHub logic runtime (seconds): {github_elapsed_seconds:.2f}")
    print(f"OpenAI LLM runtime (seconds): {openai_elapsed_seconds:.2f}")
    print(f"Total runtime (seconds): {total_elapsed_seconds:.2f}")


if __name__ == "__main__":
    main()
