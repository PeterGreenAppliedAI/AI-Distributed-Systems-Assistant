"""
DevMesh Platform - LLM Orchestration Service
Core reasoning loop using Ollama /api/chat with tool calling.

The analyze() function sends a user query to the LLM, which can call tools
(search_templates, query_logs, etc.) to gather evidence, then synthesizes
a structured answer with cited sources.
"""

import json
import os
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from errors import LLMError, LLMTimeoutError
from services.llm_tools import (
    ALL_TOOL_SCHEMAS, TOOL_EXECUTORS, execute_tool,
    execute_get_service_overview, execute_query_logs, truncate_result,
)

logger = logging.getLogger(__name__)

GATEWAY_URL = os.getenv("GATEWAY_URL", "http://192.168.1.184:8001")
LLM_MODEL = os.getenv("LLM_MODEL", "nemotron-3-nano:30b")
LLM_TIMEOUT = int(os.getenv("LLM_TIMEOUT", "300"))
LLM_MAX_ITERATIONS = int(os.getenv("LLM_MAX_ITERATIONS", "5"))

SYSTEM_PROMPT_TEMPLATE = """You are a log analysis assistant for the DevMesh observability platform.
You monitor 7 Linux nodes: dev-services, gpu-node, gpu-node-3060,
electrical-estimator, teaching, mariadb-vm, postgres-vm.
You have access to ~878K log events organized into ~5,500 canonical templates.

Current date and time: {now_utc} UTC

You have already been given:
- A service overview (last 24h) showing all services, event counts, error counts, and active hosts
- If the user specified filters (host, service, time range), scoped log data matching those filters

Use this pre-fetched data to answer immediately if possible. Only call additional tools if you need to dig deeper:
- search_templates — find log patterns semantically
- query_logs — get specific raw events with filters
- get_template_stats — detect frequency spikes or anomalies
- get_log_context — get surrounding events for a specific event ID
- get_service_overview — get a fresh overview (you already have one, only re-call if needed)

IMPORTANT: When the user says "today", use today's date ({today}). Do not guess dates.
When no time range is specified, omit start_time/end_time to get the most recent data.

IMPORTANT: You MUST use the tool calling interface to call tools. NEVER write tool calls as text in your response.
The only tools available are: search_templates, query_logs, get_template_stats, get_service_overview, get_log_context.
Do NOT invent tool names — only use the five tools listed above.

Always cite specific log messages and timestamps.
Say clearly if you cannot find relevant data.
Assess confidence as high/medium/low."""


def _build_system_prompt() -> str:
    now = datetime.now(timezone.utc)
    return SYSTEM_PROMPT_TEMPLATE.format(
        now_utc=now.strftime("%Y-%m-%d %H:%M:%S"),
        today=now.strftime("%Y-%m-%d"),
    )


@dataclass
class ToolCallRecord:
    tool_name: str
    arguments: dict
    result_summary: str


@dataclass
class AnalyzeResult:
    query: str
    explanation: str
    confidence: Optional[str]
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    iterations: int = 0
    model: str = ""


def _build_user_message(query: str, context: dict) -> str:
    """Build the user message with optional context filters."""
    parts = [query]
    filters = []
    if context.get("service"):
        filters.append(f"service: {context['service']}")
    if context.get("host"):
        filters.append(f"host: {context['host']}")
    if context.get("start_time"):
        filters.append(f"start_time: {context['start_time']}")
    if context.get("end_time"):
        filters.append(f"end_time: {context['end_time']}")
    if filters:
        parts.append("\nContext filters (use these in your tool calls): " + ", ".join(filters))
    return "\n".join(parts)


async def _call_ollama(
    http_client: httpx.AsyncClient,
    messages: list[dict],
    tools: list[dict],
) -> dict:
    """Call Ollama /api/chat and return the response message."""
    payload = {
        "model": LLM_MODEL,
        "messages": messages,
        "tools": tools,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }

    try:
        resp = await http_client.post(
            f"{GATEWAY_URL}/api/chat",
            json=payload,
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except httpx.TimeoutException as e:
        raise LLMTimeoutError(f"Ollama request timed out after {LLM_TIMEOUT}s: {e}")
    except httpx.HTTPStatusError as e:
        raise LLMError(f"Ollama returned HTTP {e.response.status_code}: {e}")
    except Exception as e:
        raise LLMError(f"Ollama request failed: {e}")


_VALID_TOOLS = set(TOOL_EXECUTORS.keys())

# Aliases the model might hallucinate -> real tool names
_TOOL_ALIASES = {
    "RunLogSearch": "query_logs",
    "run_log_search": "query_logs",
    "log_search": "query_logs",
    "search_logs": "query_logs",
    "get_logs": "query_logs",
    "search_log_templates": "search_templates",
    "search_log_template": "search_templates",
    "find_templates": "search_templates",
    "template_search": "search_templates",
    "service_overview": "get_service_overview",
    "get_overview": "get_service_overview",
    "template_stats": "get_template_stats",
    "log_context": "get_log_context",
    "get_context": "get_log_context",
}


def _try_parse_text_tool_calls(content: str) -> list[dict] | None:
    """Attempt to extract tool calls the model wrote as plain text/JSON.

    Returns a list of {name, arguments} dicts if found, else None.
    """
    if not content:
        return None

    parsed_calls = []

    # Strategy 1: Look for JSON objects with "tool"/"name" and "arguments" keys
    for m in re.finditer(r'\{[^{}]*"(?:tool|name)"[^{}]*\}', content, re.DOTALL):
        try:
            obj = json.loads(m.group())
            name = obj.get("tool") or obj.get("name") or obj.get("function") or obj.get("action")
            args = obj.get("arguments") or obj.get("params") or obj.get("parameters") or obj.get("input") or {}
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except json.JSONDecodeError:
                    args = {}
            if name and isinstance(name, str):
                # Resolve aliases or check valid
                resolved = _TOOL_ALIASES.get(name, name)
                if resolved in _VALID_TOOLS:
                    parsed_calls.append({"name": resolved, "arguments": args})
        except json.JSONDecodeError:
            continue

    # Strategy 2: Look for function-call-style patterns like tool_name({...})
    if not parsed_calls:
        for m in re.finditer(r'(\w+)\s*\(\s*(\{[^)]*\})\s*\)', content):
            name = m.group(1)
            resolved = _TOOL_ALIASES.get(name, name)
            if resolved in _VALID_TOOLS:
                try:
                    args = json.loads(m.group(2))
                    parsed_calls.append({"name": resolved, "arguments": args})
                except json.JSONDecodeError:
                    continue

    return parsed_calls if parsed_calls else None


SYNTHESIS_PROMPT = """A user asked a question about their infrastructure. Tools gathered data. Your ONLY job is to answer their question.

USER QUESTION: {query}

COLLECTED DATA:
{tool_summaries}

STRICT RULES:
1. Answer ONLY the question above. Do not answer a different question.
2. Do not give a general service overview unless that is what was asked.
3. Do not offer "next steps" or ask "what would you like to explore". Just answer.
4. If the data contains the answer, extract it and present it clearly.
5. If the data does NOT contain the answer, say so explicitly.

FORMAT:
- Direct answer first (use a markdown table if comparing things)
- 2-3 key observations (only if genuinely relevant to the question)
- Confidence: high/medium/low

Be concise. No filler. No menus of options."""


async def _synthesize(
    http_client: httpx.AsyncClient,
    query: str,
    tool_records: list[ToolCallRecord],
) -> str:
    """Phase 2: Ask the model to synthesize tool results into analysis."""
    # Build a compact summary of tool results
    summaries = []
    for i, rec in enumerate(tool_records, 1):
        summaries.append(
            f"### Tool call {i}: {rec.tool_name}({json.dumps(rec.arguments)})\n"
            f"Result:\n{rec.result_summary}"
        )
    tool_text = "\n\n".join(summaries)

    prompt = SYNTHESIS_PROMPT.format(query=query, tool_summaries=tool_text)

    payload = {
        "model": LLM_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }

    try:
        resp = await http_client.post(
            f"{GATEWAY_URL}/api/chat",
            json=payload,
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json().get("message", {}).get("content", "")
    except Exception as e:
        logger.warning("Synthesis call failed: %s", e)
        return ""


def _extract_sources(tool_records: list[ToolCallRecord]) -> list[dict]:
    """Extract cited sources from tool call results."""
    sources = []
    for record in tool_records:
        try:
            data = json.loads(record.result_summary)
            if isinstance(data, list):
                for item in data[:5]:
                    if isinstance(item, dict) and "error" not in item:
                        sources.append(item)
        except (json.JSONDecodeError, TypeError):
            pass
    return sources[:20]


async def _prefetch_context(
    pool,
    http_client: httpx.AsyncClient,
    context: dict,
) -> list[ToolCallRecord]:
    """Deterministic scaffolding: pre-fetch data before the LLM loop.

    Always fetches service overview. If user set filters, also fetches
    scoped logs matching those filters.
    """
    records = []

    # Always: service overview (last 24h)
    try:
        overview_result = await execute_get_service_overview(pool, {"hours": 24})
        overview_json = truncate_result(overview_result)
        records.append(ToolCallRecord(
            tool_name="get_service_overview",
            arguments={"hours": 24},
            result_summary=overview_json,
        ))
        logger.info("Pre-fetched service overview: %d services", len(overview_result))
    except Exception as e:
        logger.warning("Pre-fetch service overview failed: %s", e)

    # If filters are set, pre-fetch scoped logs
    log_args = {}
    if context.get("host"):
        log_args["host"] = context["host"]
    if context.get("service"):
        log_args["service"] = context["service"]
    if context.get("start_time"):
        log_args["start_time"] = context["start_time"]
    if context.get("end_time"):
        log_args["end_time"] = context["end_time"]

    if log_args:
        log_args["limit"] = 30
        try:
            log_result = await execute_query_logs(pool, log_args)
            log_json = truncate_result(log_result)
            records.append(ToolCallRecord(
                tool_name="query_logs",
                arguments=log_args,
                result_summary=log_json,
            ))
            logger.info("Pre-fetched scoped logs: %d events", len(log_result))
        except Exception as e:
            logger.warning("Pre-fetch scoped logs failed: %s", e)

    return records


async def analyze(
    http_client: httpx.AsyncClient,
    pool,
    query: str,
    context: Optional[dict] = None,
) -> AnalyzeResult:
    """Run the LLM reasoning loop with tool calling.

    Phase 1 (deterministic): Pre-fetch service overview and scoped logs.
    Phase 2 (dynamic): LLM reasons over pre-fetched data, calls more tools if needed.
    Phase 3 (synthesis): If the LLM's answer is weak, run a dedicated synthesis call.

    Args:
        http_client: Shared httpx client for Ollama calls
        pool: Async DB connection pool
        query: User's natural language question
        context: Optional dict with service, host, start_time, end_time filters

    Returns:
        AnalyzeResult with explanation, tool calls, sources, and metadata
    """
    context = context or {}

    # Phase 1: Deterministic pre-fetch
    tool_records = await _prefetch_context(pool, http_client, context)

    # Build conversation with pre-fetched data injected
    messages = [
        {"role": "system", "content": _build_system_prompt()},
        {"role": "user", "content": _build_user_message(query, context)},
    ]

    # Inject pre-fetched results as if the assistant called tools
    if tool_records:
        # Fake assistant message requesting the tools
        prefetch_tool_calls = [
            {"function": {"name": rec.tool_name, "arguments": rec.arguments}}
            for rec in tool_records
        ]
        messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": prefetch_tool_calls,
        })
        # Add each tool result
        for rec in tool_records:
            messages.append({
                "role": "tool",
                "content": rec.result_summary,
            })

    # Phase 2: Dynamic LLM loop (model starts with context already loaded)
    best_candidate = ""  # Track best substantive answer seen during the loop

    for iteration in range(LLM_MAX_ITERATIONS):
        response = await _call_ollama(http_client, messages, ALL_TOOL_SCHEMAS)

        message = response.get("message", {})
        content = message.get("content", "")
        tool_calls = message.get("tool_calls")

        # If the model produced content alongside tool calls, save it as a candidate
        if content and tool_calls and len(content.strip()) > len(best_candidate.strip()):
            _lower = content.strip().lower()
            is_deflection = any(p in _lower for p in [
                "let me know", "what would you like", "i'm all set",
                "i'm ready", "would you like", "shall i", "happy to help",
            ])
            if not is_deflection:
                best_candidate = content
                logger.info("Saved candidate answer (%d chars) from iteration %d",
                            len(content.strip()), iteration + 1)

        if not tool_calls:
            # Check if the model wrote tool calls as text instead
            recovered = _try_parse_text_tool_calls(content)
            if recovered:
                logger.info("Recovered %d tool call(s) from text content", len(recovered))
                # Synthesize a proper tool_calls list and continue the loop
                tool_calls = [
                    {"function": {"name": tc["name"], "arguments": tc["arguments"]}}
                    for tc in recovered
                ]
                # Replace the message content so the model doesn't see its own JSON
                message = dict(message)
                message["tool_calls"] = tool_calls
                message["content"] = ""
            else:
                # Final answer — no more tool calls
                explanation = content
                # Detect weak answers: too short, or polite deflections
                needs_synthesis = False
                if tool_records:
                    stripped = content.strip().lower()
                    if len(stripped) < 200:
                        needs_synthesis = True
                        logger.info("Short final answer (%d chars), running synthesis", len(stripped))
                    elif any(phrase in stripped for phrase in [
                        "let me know",
                        "just let me know",
                        "what would you like",
                        "i'm all set",
                        "i'm ready",
                        "would you like me to",
                        "shall i",
                        "happy to help",
                        "i can pull",
                        "i can query",
                    ]):
                        needs_synthesis = True
                        logger.info("Deflection detected in final answer, running synthesis")

                if needs_synthesis:
                    # First try: use a good candidate answer from an earlier iteration
                    if best_candidate:
                        logger.info("Using saved candidate answer instead of deflection")
                        explanation = best_candidate
                    else:
                        synthesized = await _synthesize(http_client, query, tool_records)
                        if synthesized:
                            explanation = synthesized

                sources = _extract_sources(tool_records)
                confidence = None
                explanation_lower = explanation.lower()
                for level in ("high", "medium", "low"):
                    if f"confidence: {level}" in explanation_lower or f"confidence is {level}" in explanation_lower:
                        confidence = level
                        break

                return AnalyzeResult(
                    query=query,
                    explanation=explanation,
                    confidence=confidence,
                    tool_calls=tool_records,
                    sources=sources,
                    iterations=iteration + 1,
                    model=LLM_MODEL,
                )

        # Append the assistant message (with tool calls) to conversation
        messages.append(message)

        # Execute each tool call
        for tc in tool_calls:
            func = tc.get("function", {})
            tool_name = func.get("name", "unknown")
            arguments = func.get("arguments", {})

            # Ensure arguments is a dict (Ollama may send it as a string)
            if isinstance(arguments, str):
                try:
                    arguments = json.loads(arguments)
                except json.JSONDecodeError:
                    arguments = {}

            try:
                result_json = await execute_tool(pool, http_client, tool_name, arguments)
            except Exception as e:
                logger.warning("Tool %s failed: %s", tool_name, e)
                result_json = json.dumps([{"error": str(e)}])

            tool_records.append(ToolCallRecord(
                tool_name=tool_name,
                arguments=arguments,
                result_summary=result_json,
            ))

            # Send tool result back to LLM
            messages.append({
                "role": "tool",
                "content": result_json,
            })

    # Hit iteration limit — use best candidate, or synthesize whatever we collected
    explanation = ""
    if best_candidate:
        logger.info("Hit iteration limit, using saved candidate answer")
        explanation = best_candidate
    elif tool_records:
        logger.info("Hit iteration limit, running synthesis over %d tool calls", len(tool_records))
        explanation = await _synthesize(http_client, query, tool_records)

    if not explanation:
        explanation = ("Analysis incomplete — reached maximum iteration limit. "
                       "The data gathered so far may be partial.")

    sources = _extract_sources(tool_records)
    confidence = None
    for level in ("high", "medium", "low"):
        if f"confidence: {level}" in explanation.lower() or f"confidence is {level}" in explanation.lower():
            confidence = level
            break

    return AnalyzeResult(
        query=query,
        explanation=explanation,
        confidence=confidence or "low",
        tool_calls=tool_records,
        sources=sources,
        iterations=LLM_MAX_ITERATIONS,
        model=LLM_MODEL,
    )
