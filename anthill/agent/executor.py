from __future__ import annotations

import json
import time
from collections.abc import Iterator
from dataclasses import dataclass, field

from ..inference.base import BackendError, Message
from .tools import Tool

# Cap on a single agent step's generation (a tool-call decision or the final answer). Generous - it
# never truncates a real step, incl. a reasoning model's think phase - but it bounds a runaway so one
# step can't blow a task's whole time budget (the chat-hang runaway, on the tool-calling path).
STEP_MAX_TOKENS = 4096


@dataclass
class StepResult:
    step: int
    tool_name: str
    arguments: dict
    result: str
    duration_ms: int
    verify_flag: str = (
        ""  # advisory: a reason string when the verifier flagged this action, else ""
    )


@dataclass
class AgentResult:
    answer: str
    steps: list[StepResult] = field(default_factory=list)
    aborted: bool = False
    abort_reason: str = ""

    @property
    def used_tools(self) -> list[str]:
        return [s.tool_name for s in self.steps]


class AgentExecutor:
    """ReAct-style tool-calling loop.

    The model receives the goal, decides which tool to call, sees the result,
    and repeats until it produces a final answer or hits max_steps.

    Ollama's native tool-call format is used (qwen3:8b and deepseek-r1:8b
    both support it natively).

    Approval gates: tools marked needs_approval=True pause and call the
    on_approval_needed callback before executing. The callback returns True
    to proceed, False to skip and inform the model. Fail-closed: a gated tool
    NEVER runs without an explicit approval - if no on_approval_needed callback
    is wired (a headless/unattended run: scheduler, A2A, chat), the action is
    blocked, not run. Otherwise a tool an org marked "needs approval" would
    execute silently on exactly the paths that have no human to approve it.
    """

    def __init__(
        self,
        backend,  # OllamaBackend
        tools: list[Tool],
        *,
        model: str | None = None,
        max_steps: int = 12,
        on_step: callable | None = None,  # called after each step
        on_approval_needed: callable | None = None,  # for tools needing human OK
        identity=None,  # AgentPrincipal | None (least-privilege)
        on_action: callable | None = None,  # audit hook per tool call
        on_action_verify: callable | None = None,  # advisory cross-check of a consequential action
        skills=None,  # list[Skill] | None - matched per run
        principles: str = "",  # always-on guiding principles (all scopes)
        max_context_tokens: int
        | None = None,  # compact past this; per-model window when None (#277)
    ) -> None:
        self.backend = backend
        self.tools = {t.name: t for t in tools}
        self.model = model
        self.max_steps = max_steps
        self.on_step = on_step
        self.on_approval_needed = on_approval_needed
        self.identity = identity  # AgentPrincipal | None
        self.on_action = on_action  # callback(identity, tool, scope, allowed)
        self.on_action_verify = on_action_verify  # callback(tool, args, result, *, goal, context)
        self.skills = skills or []  # Agent Skills available to match
        self.principles = principles or ""  # always-on principles (org/team/personal)
        from ..inference.context import token_budget

        # In-session compaction budget: an explicit value wins, else derive it from the model's context
        # window (falls back to the historical 6000 floor when the window is unknown) (#277).
        self.max_context_tokens = (
            max_context_tokens if max_context_tokens is not None else token_budget(backend, model)
        )

    def _approval_block(self, tool, tool_name: str, arguments) -> str | None:
        """Approval decision for a tool call. Returns None to run it, or a substitute result string
        (the tool does NOT run) otherwise. Fail-closed: a needs_approval tool with no approver wired
        is blocked, never run - so a gated action can't slip through on the headless paths."""
        if not tool.needs_approval:
            return None
        if self.on_approval_needed is None:
            return f"(blocked - {tool_name} requires approval but no approver is available)"
        if not self.on_approval_needed(tool_name, arguments):
            return f"(skipped - user did not approve {tool_name})"
        return None

    def _maybe_compact(self, messages):
        """Summarize older turns when the running context exceeds the budget."""
        try:
            from .compact import compact_messages

            return compact_messages(
                messages, self.backend, max_tokens=self.max_context_tokens, model=self.model
            )
        except Exception:
            return messages

    def _authorize(self, tool_name: str) -> bool:
        """Per-agent least-privilege check + audit. True if the call may proceed."""
        from .tools import tool_scope

        if self.identity is None:
            return True
        allowed = self.identity.allows(tool_name)
        if self.on_action is not None:
            self.on_action(self.identity.name, tool_name, tool_scope(tool_name), allowed)
        return allowed

    def _verify_action(self, tool_name, arguments, result, *, goal, context) -> str:
        """Advisory cross-check of a consequential action just taken (the verifier). Returns a short
        reason when an independent model judges the action inconsistent with the goal, else "". Purely
        advisory - the action already ran; this only surfaces a warning. NEVER raises. See anthill.verify.
        """
        if not self.on_action_verify:
            return ""
        try:
            verdict = self.on_action_verify(
                tool_name, arguments, result, goal=goal, context=context
            )
        except Exception:
            return ""
        if verdict is not None and not verdict.ok:
            return (verdict.reason or "flagged")[:200]
        return ""

    def run(self, goal: str, *, context: str = "") -> AgentResult:
        """Run the agent synchronously and return the full result."""
        messages = self._build_initial_messages(goal, context)
        steps: list[StepResult] = []

        for step_num in range(self.max_steps):
            messages = self._maybe_compact(messages)
            response = self._chat_with_tools(messages)

            # Check for tool calls
            tool_calls = self._parse_tool_calls(response)
            if not tool_calls:
                # No tool call → model has produced its final answer
                answer = self._extract_content(response)
                return AgentResult(answer=answer, steps=steps)

            # Execute each tool call
            for tc in tool_calls:
                tool_name, arguments = self._call_fields(tc)

                tool = self.tools.get(tool_name)
                if not tool:
                    result_str = f"Unknown tool: {tool_name}"
                elif not self._authorize(tool_name):
                    result_str = (
                        f"(blocked - agent '{self.identity.name}' is not "
                        f"permitted to use {tool_name})"
                    )
                else:
                    ran = False
                    dur_ms = 0
                    # Approval gate (fail-closed: no approver wired -> blocked, not run)
                    block = self._approval_block(tool, tool_name, arguments)
                    if block is not None:
                        result_str = block
                    else:
                        t0 = time.monotonic()
                        result_str = tool.call(arguments)
                        dur_ms = int((time.monotonic() - t0) * 1000)
                        ran = True

                    # Advisory verifier: only for consequential actions that actually ran.
                    verify_flag = (
                        self._verify_action(
                            tool_name, arguments, result_str, goal=goal, context=context
                        )
                        if (ran and tool.needs_approval)
                        else ""
                    )
                    step = StepResult(
                        step=step_num + 1,
                        tool_name=tool_name,
                        arguments=arguments,
                        result=result_str[:2000],  # cap for context window
                        duration_ms=dur_ms if tool.needs_approval is False else 0,
                        verify_flag=verify_flag,
                    )
                    steps.append(step)
                    if self.on_step:
                        self.on_step(step)

                # Append tool result to conversation
                messages.append(Message("assistant", self._extract_content(response), images=None))
                messages.append(
                    Message("tool", json.dumps({"tool": tool_name, "result": result_str[:2000]}))
                )

        # Max steps reached
        return AgentResult(
            answer="I reached the maximum number of steps without completing the task. "
            "Here is what I found so far:\n\n"
            + "\n\n".join(f"**{s.tool_name}**: {s.result[:300]}" for s in steps),
            steps=steps,
            aborted=True,
            abort_reason="max_steps",
        )

    def stream(self, goal: str, *, context: str = "") -> Iterator[str]:
        """Stream the agent's progress as text chunks.

        Yields human-readable progress lines so the chat UI can display
        what the agent is doing in real time.
        """

        messages = self._build_initial_messages(goal, context)

        for step_num in range(self.max_steps):
            yield f"\n🔍 **Step {step_num + 1}** - thinking...\n"

            messages = self._maybe_compact(messages)
            response = self._chat_with_tools(messages)
            tool_calls = self._parse_tool_calls(response)

            if not tool_calls:
                answer = self._extract_content(response)
                yield f"\n{answer}"
                return

            for tc in tool_calls:
                tool_name, arguments = self._call_fields(tc)

                yield f"\n🛠️ **{tool_name}**({', '.join(f'{k}={repr(v)[:40]}' for k, v in arguments.items())})\n"

                tool = self.tools.get(tool_name)
                if not tool:
                    result_str = f"Unknown tool: {tool_name}"
                elif not self._authorize(tool_name):
                    result_str = (
                        f"(blocked - agent '{self.identity.name}' lacks permission for {tool_name})"
                    )
                else:
                    ran = False
                    # Approval gate (fail-closed: no approver wired -> blocked, not run)
                    block = self._approval_block(tool, tool_name, arguments)
                    if block is not None:
                        result_str = block
                    else:
                        result_str = tool.call(arguments)
                        ran = True

                yield f"✓ {result_str[:200]}{'...' if len(result_str) > 200 else ''}\n"
                # Advisory verifier: warn inline when an independent model disputes a consequential action.
                if ran and tool.needs_approval:
                    vflag = self._verify_action(
                        tool_name, arguments, result_str, goal=goal, context=context
                    )
                    if vflag:
                        yield f"\n⚠️ verifier: {vflag}\n"

                messages.append(Message("assistant", self._extract_content(response)))
                messages.append(
                    Message("tool", json.dumps({"tool": tool_name, "result": result_str[:2000]}))
                )

        yield "\n⚠️ Reached maximum steps."

    # ── internals ─────────────────────────────────────────────────────────────

    def _build_initial_messages(self, goal: str, context: str) -> list[Message]:
        system = (
            "You are a capable AI assistant with access to tools. "
            "Use tools to gather information before answering. "
            "When you have enough information, give a clear, complete answer. "
            "Always cite your sources (URLs, wiki slugs). "
            "For tasks requiring action (sending email, writing to wiki), "
            "draft the action and describe what you did."
        )
        # Guiding principles: always-on directives for this org/team/user. Injected
        # before skills so they take precedence in the system prompt.
        if self.principles:
            system += (
                "\n\n## Standing principles\n"
                "Follow these in every response. Organization principles are "
                "authoritative; team and personal principles refine them.\n" + self.principles
            )
        # Agent Skills: inject instructions for skills relevant to this goal, but
        # only those whose declared scopes the agent's identity actually holds -
        # so an agent can't follow a skill it isn't permitted to act on.
        if self.skills:
            try:
                from .skills import match_skills, skills_system_prompt

                matched = match_skills(self.skills, goal)
                if self.identity is not None:
                    matched = [
                        s
                        for s in matched
                        if all(sc in self.identity.scopes for sc in (s.scopes or []))
                    ]
                extra = skills_system_prompt(matched)
                if extra:
                    system += "\n\n" + extra
            except Exception:
                pass
        user = goal if not context else f"CONTEXT:\n{context}\n\nTASK: {goal}"
        return [Message("system", system), Message("user", user)]

    def _chat_with_tools(self, messages: list[Message]) -> dict:
        """Call the backend with tool specs and return an Ollama-shaped response dict.

        Delegates to ``backend.chat_with_tools`` (implemented by both the Ollama and the
        OpenAI-compatible backends), so an org/cloud chat gets the SAME tool-calling as local
        Ollama - previously a non-Ollama backend silently fell back to plain, tool-less chat.
        A backend without tool support degrades to plain chat. ``BackendError`` carries a
        human-readable message (e.g. 'Can't reach Ollama at localhost:11434') and propagates so
        unattended runs cannot record a failed inference as a successful answer."""
        specs = [t.ollama_spec() for t in self.tools.values()]
        cwt = getattr(self.backend, "chat_with_tools", None)
        try:
            if cwt is not None:
                # Bound each agent step (a reasoning model can otherwise run away and blow a task's
                # time budget - the same runaway as the chat hang, but on the tool-calling path).
                try:
                    return cwt(
                        messages, specs, model=self.model or None, num_predict=STEP_MAX_TOKENS
                    )
                except TypeError:  # a backend whose chat_with_tools doesn't accept num_predict
                    return cwt(messages, specs, model=self.model or None)
            return {"message": {"content": self.backend.chat(messages)}}
        except BackendError:
            raise
        except Exception as e:
            raise BackendError(f"Backend error: {e}") from e

    def _parse_tool_calls(self, response: dict) -> list[dict]:
        msg = response.get("message", {})
        return msg.get("tool_calls", [])

    def _call_fields(self, tc: dict) -> tuple[str, dict]:
        """Tool name + parsed arguments from one tool-call dict. Single source for run() and
        stream() so the two ReAct loops cannot drift on the extraction (they had: stream() used a
        bogus nested ``function.function.name`` lookup that was always None, masked by an `or`)."""
        fn = tc.get("function", {})
        raw_args = fn.get("arguments", "{}")
        arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        return fn.get("name", ""), arguments

    def _extract_content(self, response: dict) -> str:
        return response.get("message", {}).get("content", "").strip()
