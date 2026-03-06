#!/usr/bin/env python3
"""Pull unresolved GitHub PR review comments and prepare implementation guidance."""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
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
              side
              startSide
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
          side
          startSide
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
        help="Optional markdown output file path for easy copy/paste.",
    )
    parser.add_argument(
        "--max-comments",
        type=int,
        default=600,
        help="Maximum unresolved comments to include in LLM input (default: 600).",
    )
    return parser.parse_args()


def parse_repository(repository: str) -> tuple[str, str]:
    if "/" not in repository:
        raise ValueError("Repository must be in 'owner/repo' format.")
    owner, name = repository.split("/", 1)
    if not owner or not name:
        raise ValueError("Repository must be in 'owner/repo' format.")
    return owner, name


def github_graphql(token: str, query: str, variables: dict[str, Any]) -> dict[str, Any]:
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
        raise RuntimeError(f"GitHub API HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"GitHub API request failed: {exc}") from exc

    result = json.loads(raw)
    if "errors" in result and result["errors"]:
        raise RuntimeError(f"GitHub GraphQL error: {result['errors']}")
    return result.get("data", {})


def fetch_all_thread_comments(token: str, thread_id: str, first_page: dict[str, Any]) -> list[dict[str, Any]]:
    comments = list(first_page.get("nodes", []))
    page_info = first_page.get("pageInfo") or {}
    cursor = page_info.get("endCursor")
    has_next = bool(page_info.get("hasNextPage"))

    while has_next:
        data = github_graphql(
            token=token,
            query=THREAD_COMMENTS_QUERY,
            variables={"threadId": thread_id, "commentsCursor": cursor},
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
        "side": comment.get("side"),
        "start_side": comment.get("startSide"),
        "outdated": bool(comment.get("outdated")),
        "code_snippet": comment.get("diffHunk", ""),
    }


def fetch_unresolved_pr_comments(
    token: str,
    owner: str,
    repo: str,
    pr_number: int,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    threads_cursor: str | None = None
    unresolved_comments: list[dict[str, Any]] = []
    pr_info: dict[str, Any] | None = None

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
        for thread in review_threads.get("nodes", []):
            if thread.get("isResolved"):
                continue
            first_page = thread.get("comments") or {}
            comments = fetch_all_thread_comments(
                token=token,
                thread_id=thread.get("id"),
                first_page=first_page,
            )
            for comment in comments:
                unresolved_comments.append(normalize_comment(thread, comment))

        page_info = review_threads.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        threads_cursor = page_info.get("endCursor")

    return pr_info or {}, unresolved_comments


def build_llm_payload(pr_info: dict[str, Any], comments: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "pull_request": pr_info,
        "guidance": {
            "goal": (
                "Summarize requested code changes from unresolved review comments and provide "
                "technical implementation guidance."
            ),
            "audience": "An LLM that will implement the code changes.",
        },
        "unresolved_comments": comments,
    }


def maybe_load_agents_locally() -> None:
    """Allow running from this repo without pip installing openai-agents."""
    if "agents" in sys.modules:
        return
    local_src = Path(__file__).resolve().parents[1] / "openai-agents-python" / "src"
    if local_src.exists():
        sys.path.insert(0, str(local_src))


def analyze_with_openai_agents(model: str, payload: dict[str, Any]) -> Any:
    maybe_load_agents_locally()
    try:
        from pydantic import BaseModel, Field
        from agents import Agent, Runner, set_tracing_disabled
    except ImportError as exc:
        raise RuntimeError(
            "Missing dependencies. Install with: pip install openai-agents pydantic"
        ) from exc

    class CommentGuidance(BaseModel):
        comment_id: str = Field(description="GitHub GraphQL comment ID from input.")
        file_path: str = Field(description="File path associated with the comment.")
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
            2. Preserve and echo input comment IDs in each entry.
            3. Keep guidance implementation-focused and avoid generic advice.
            4. If a comment is ambiguous, state assumptions explicitly in technical_explanation.
            """
        ).strip(),
        output_type=PRGuidance,
    )

    set_tracing_disabled(True)
    message = (
        "Analyze the following unresolved GitHub PR comments payload and produce structured "
        "guidance.\n\n"
        f"{json.dumps(payload, indent=2)}"
    )
    result = Runner.run_sync(agent, message)
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

                ID: `{comment.comment_id}`
                File: `{comment.file_path}`
                """
            ).strip()
        )
        sections.append(block("Requested Change Summary", comment.requested_change_summary))
        sections.append(block("Technical Explanation", comment.technical_explanation))
        sections.append(block("Implementation Prompt", comment.implementation_prompt))

    return "\n\n".join(sections).rstrip() + "\n"


def main() -> None:
    args = parse_args()
    try:
        owner, repo = parse_repository(args.repository)
    except ValueError as exc:
        raise SystemExit(str(exc))

    if not args.github_token:
        raise SystemExit(
            "Missing GitHub token. Set GITHUB_TOKEN or provide --github-token."
        )
    if not os.environ.get("OPENAI_API_KEY"):
        raise SystemExit("Missing OPENAI_API_KEY environment variable.")

    pr_info, comments = fetch_unresolved_pr_comments(
        token=args.github_token,
        owner=owner,
        repo=repo,
        pr_number=args.pr_number,
    )

    if not comments:
        print(
            textwrap.dedent(
                f"""\
                No unresolved pull request review comments were found.
                Repository: {owner}/{repo}
                Pull Request: #{args.pr_number}
                """
            ).strip()
        )
        return

    comments = comments[: args.max_comments]
    payload = build_llm_payload(pr_info=pr_info, comments=comments)
    analysis = analyze_with_openai_agents(model=args.model, payload=payload)
    report = render_report(analysis=analysis, pr_info=pr_info, comments_count=len(comments))

    print(report)
    if args.output_file:
        Path(args.output_file).write_text(report, encoding="utf-8")
        print(f"Saved report to: {args.output_file}")


if __name__ == "__main__":
    main()
