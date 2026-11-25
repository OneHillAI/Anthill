"""Where an item was used: the answers whose record lists it. Spec: docs/specs/answer-record-everywhere.md.

Only answers the viewer may already open are returned: their own chats, and the runs of the tasks and agents they created. Nothing here
widens who sees an answer. The record is read as stored (ids, versions, hashes, never text), and the wording is "used in", never "caused".
"""

from __future__ import annotations

from ..wiki import answer_record
from .db import Agent, AgentRun, ChatMessage, Conversation, ScheduledTask, TaskRun

LIMIT = 50  # the newest answers; the scan is bounded by the viewer's own answers


def _given(stored: str, item_id: str) -> dict | None:
    """The item with ``item_id`` in a stored record, or None. The text is checked as a whole first so a record that cannot hold the id
    is not parsed."""
    if not stored or item_id not in stored:
        return None
    for item in answer_record.loads(stored)["items"]:
        if isinstance(item, dict) and item.get("id") == item_id:
            return item
    return None


def used_in(db, item_id: str, *, user_id: int, org_id: int | None) -> list[dict]:
    """The answers (chat, task run, agent run) the viewer owns whose record lists ``item_id``, newest first. Each row says the route, the
    title, the time, the version of the item the answer was given and whether the item still stands as it was then (``unchanged``,
    ``changed``, ``gone``, ``not_held`` or ``unknown``)."""
    if not item_id:
        return []
    rows: list[dict] = []

    def add(route, title, at, stored, ref):
        item = _given(stored, item_id)
        if item is None:
            return
        status = next(
            (s["status"] for s in answer_record.changed_since({"items": [item]})), "unknown"
        )
        rows.append(
            {
                "route": route,
                "title": title,
                "at": at,
                "version": item.get("version", 0),
                "status": status,
                "ref": ref,
            }
        )

    chats = (
        db.query(ChatMessage, Conversation)
        .join(Conversation, Conversation.id == ChatMessage.conversation_id)
        .filter(
            Conversation.user_id == user_id,
            Conversation.org_id == org_id,
            ChatMessage.role == "assistant",
            ChatMessage.manifest.contains(item_id),
        )
        .order_by(ChatMessage.id.desc())
        .limit(LIMIT)
    )
    for msg, conv in chats:
        add(
            "chat",
            conv.title,
            msg.created_at,
            msg.manifest,
            {"conversation": conv.id, "message": msg.id},
        )
    task_runs = (
        db.query(TaskRun, ScheduledTask)
        .join(ScheduledTask, ScheduledTask.id == TaskRun.task_id)
        .filter(
            ScheduledTask.created_by == user_id,
            ScheduledTask.org_id == org_id,
            TaskRun.record.contains(item_id),
        )
        .order_by(TaskRun.id.desc())
        .limit(LIMIT)
    )
    for run, task in task_runs:
        add("task", task.title, run.finished_at, run.record, {"task": task.id, "run": run.id})
    agent_runs = (
        db.query(AgentRun, Agent)
        .join(Agent, Agent.id == AgentRun.agent_id)
        .filter(
            Agent.created_by == user_id,
            Agent.org_id == org_id,
            AgentRun.record.contains(item_id),
        )
        .order_by(AgentRun.id.desc())
        .limit(LIMIT)
    )
    for run, agent in agent_runs:
        add("agent", agent.name, run.finished_at, run.record, {"agent": agent.id, "run": run.id})
    rows.sort(key=lambda r: str(r["at"] or ""), reverse=True)
    return rows
