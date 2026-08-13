# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Working Guidelines

1. **Think first, then read**: Before making any changes, think through the problem and read relevant files in the codebase.

2. **Check in before major changes**: Before making any major changes, check in with the user to verify the plan.

3. **Explain changes at a high level**: At every step, provide a high-level explanation of what changes were made.

4. **Keep it simple**: Make every task and code change as simple as possible. Avoid massive or complex changes. Every change should impact as little code as possible. Simplicity is paramount.

5. **Maintain architecture documentation**: Keep a documentation file that describes how the architecture of the app works inside and out.

6. **Never speculate about unread code**: Never make claims about code you haven't opened. If a specific file is referenced, read it before answering. Investigate and read relevant files BEFORE answering questions about the codebase. Give grounded, hallucination-free answers.

## Architecture and Operations

**See [CLAUDE.md](CLAUDE.md)** for the project overview, backend and frontend
architecture, configuration constants, WebSocket events, MQTT topics, Docker
layout, and video runner recovery.

`CLAUDE.md` is the single source of truth for all of that. Do not duplicate it
here.

> This file previously carried its own full copy of that documentation. The copy
> went stale and, on 2026-08-12, was found still instructing agents to install a
> host cron job that restarted the video runner every 2 minutes. That cron job
> had been silently corrupting detection clips since the App Lab SDK update —
> see `project_plans/video_clip_duration_fix.md`. Keeping one copy is what
> prevents a repeat.

## Planning and Documentation Rules

- **All implementation plans must be written to `project_plans/`** in the project root. When creating plans for new features or changes, write them as markdown files in `project_plans/` (e.g., `project_plans/persistent_settings_plan.md`).
- When modifying `task.md`, `implementation_plan.md`, or `walkthrough.md` in internal planning directories, also copy them to `project_plans/`.
