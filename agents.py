"""Multi-agent support system with structured output, conversation memory, RAG, tool calling, and guardrails."""

import re
from typing import TypedDict, Annotated, Literal, Optional
from operator import add
from datetime import datetime

from dotenv import load_dotenv
load_dotenv()

from langchain_groq import ChatGroq
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.messages import SystemMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, START, END
from pydantic import BaseModel, Field

from observability import trace_llm_call
from rag import retrieve, format_context, format_sources
from tools import DOMAIN_TOOLS

llm = ChatGroq(model="llama-3.3-70b-versatile", temperature=0)

MAX_TOOL_ITERATIONS = 3   # max tool-calling rounds per worker
MAX_QA_RETRIES = 1        # max times QA can send worker back for self-correction


# --- Structured Output Models ---

class Classification(BaseModel):
    """Supervisor classification of an employee support request."""
    category: Literal["hr", "tech", "finance", "facilities", "general"] = Field(
        description="The department that should handle this request"
    )
    confidence: int = Field(
        ge=1, le=10,
        description="How confident you are in this classification, from 1 to 10"
    )


class WorkerResponse(BaseModel):
    """Domain worker response to an employee request."""
    response: str = Field(
        description="A helpful 2-3 sentence response to the employee's request, grounded in the policy documents provided"
    )
    needs_escalation: bool = Field(
        default=False,
        description="Whether this request needs manager approval or escalation"
    )
    escalation_reason: Optional[str] = Field(
        default=None,
        description="Reason for escalation, if needed"
    )


class SupportRequest(TypedDict):
    employee_name: str
    request: str
    conversation_history: list[dict]  # prior turns: [{"role": "user"|"assistant", "content": "..."}]
    category: str
    confidence: int
    rag_context: str        # retrieved policy text injected into worker prompts
    rag_sources: list[str]  # source citations from RAG retrieval
    worker_output: str
    needs_escalation: bool
    escalation_reason: str
    tool_calls_made: list[str]  # log of tool calls made during this request
    react_iterations: int   # how many ReAct iterations the worker used
    qa_retry_count: int     # how many times QA has sent the worker back for correction
    qa_feedback: str        # QA feedback for self-correction retry
    error: str
    fallback_used: bool
    final_response: str
    audit: Annotated[list, add]


# --- Conversation History Formatting ---

MAX_HISTORY_TURNS = 10  # max prior messages to include in prompts


def format_history(state: SupportRequest) -> str:
    """Format conversation history into a readable string for prompt injection."""
    history = state.get("conversation_history") or []
    if not history:
        return ""
    # Take last MAX_HISTORY_TURNS messages
    recent = history[-MAX_HISTORY_TURNS:]
    lines = ["Previous conversation:"]
    for msg in recent:
        role = "Employee" if msg["role"] == "user" else "Support"
        lines.append(f"  {role}: {msg['content'][:300]}")
    return "\n".join(lines)


# --- Supervisor with Structured Output ---

SUPERVISOR_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessage(content=(
        "You are the UniGPS support desk supervisor. Your job is to classify "
        "employee requests into the correct department.\n\n"
        "Departments:\n"
        "- hr: Leave, attendance, WFH, insurance, onboarding, policies, appraisals\n"
        "- tech: IT support, VPN, Jira, AWS, software, hardware, production issues\n"
        "- finance: Salary, expenses, reimbursements, tax, invoices\n"
        "- facilities: Desks, parking, cafeteria, access cards, building, maintenance\n"
        "- general: Anything that doesn't fit the above categories\n\n"
        "Examples:\n"
        "- 'I need to apply for 3 days leave' → hr, confidence 9\n"
        "- 'My VPN is not connecting' → tech, confidence 9\n"
        "- 'When will I get my salary slip?' → finance, confidence 8\n"
        "- 'The AC in meeting room 3 is not working' → facilities, confidence 9\n"
        "- 'Hello, how are you?' → general, confidence 7\n"
        "- 'something something' → general, confidence 3\n\n"
        "Use the conversation history (if any) to understand context. "
        "For example, if the employee says 'yes' or 'that one', look at the prior "
        "messages to understand what they are referring to."
    )),
    ("human", "{history}\nEmployee: {employee_name}\nRequest: {request}"),
])

supervisor_llm = llm.with_structured_output(Classification)


def supervisor(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        prompt = SUPERVISOR_PROMPT.format_messages(
            history=format_history(state),
            employee_name=state["employee_name"],
            request=state["request"],
        )
        with trace_llm_call("supervisor") as ctx:
            result = supervisor_llm.invoke(prompt)
            ctx["response"] = result
        return {
            "category": result.category,
            "confidence": result.confidence,
            "error": "",
            "audit": [f"[{ts}] Supervisor: {result.category} (conf: {result.confidence})"],
        }
    except Exception as e:
        return {
            "category": "general",
            "confidence": 1,
            "error": "",
            "audit": [f"[{ts}] Supervisor error ({e}), fallback to general"],
        }


def route_supervisor(state: SupportRequest) -> str:
    if state["confidence"] < 5:
        return "clarify"
    return state["category"]


# --- RAG Retrieval Node ---

def rag_retrieval(state: SupportRequest) -> dict:
    """Retrieve relevant policy documents based on the query and category."""
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        results = retrieve(
            query=state["request"],
            category=state["category"],
            top_k=4,
        )
        context = format_context(results)
        sources = format_sources(results)
        return {
            "rag_context": context,
            "rag_sources": sources,
            "audit": [f"[{ts}] RAG: retrieved {len(results)} chunks from {len(sources)} sources"],
        }
    except Exception as e:
        return {
            "rag_context": "",
            "rag_sources": [],
            "audit": [f"[{ts}] RAG error ({e}), continuing without context"],
        }


# --- Domain Worker Factory ---

WORKER_CONFIGS = {
    "hr": {
        "system_prompt": (
            "You are UniGPS HR (Gheware UniGPS Solutions LLP).\n"
            "Escalate if: >10 days leave request, policy exceptions, or special cases."
        ),
        "can_escalate": True,
    },
    "tech": {
        "system_prompt": (
            "You are UniGPS IT Support.\n"
            "Escalate if: P1 severity (production down, data loss, security breach)."
        ),
        "can_escalate": True,
    },
    "finance": {
        "system_prompt": (
            "You are UniGPS Finance team."
        ),
        "can_escalate": False,
    },
    "facilities": {
        "system_prompt": (
            "You are UniGPS Facilities/Admin team."
        ),
        "can_escalate": False,
    },
}

worker_llm = llm.with_structured_output(WorkerResponse)


def _execute_tool_calls(ai_message: AIMessage, tools_by_name: dict) -> list[ToolMessage]:
    """Execute tool calls from an AI message and return ToolMessage results."""
    results = []
    for tc in ai_message.tool_calls:
        tool_fn = tools_by_name.get(tc["name"])
        if tool_fn:
            try:
                output = tool_fn.invoke(tc["args"])
            except Exception as e:
                output = f"Tool error: {e}"
        else:
            output = f"Unknown tool: {tc['name']}"
        results.append(ToolMessage(content=str(output), tool_call_id=tc["id"]))
    return results


def make_domain_worker(name: str, system_prompt: str, can_escalate: bool):
    """Factory that creates a domain worker with ReAct (Think-Act-Observe) loop and tools."""

    domain_tools = DOMAIN_TOOLS.get(name, [])
    tools_by_name = {t.name: t for t in domain_tools}

    tool_instructions = ""
    if domain_tools:
        tool_names = ", ".join(t.name for t in domain_tools)
        tool_instructions = (
            f"\n\nYou have access to these tools: {tool_names}. "
            "Follow this reasoning process:\n"
            "1. THINK: What information do I need? Can I answer from the policy docs, or do I need to look up real data?\n"
            "2. ACT: If you need real data, call the appropriate tool. If you can answer from policies, respond directly.\n"
            "3. OBSERVE: Review the tool result. Is it sufficient, or do you need another tool call?\n"
            "Repeat until you have enough information to give a complete answer. "
            "Do NOT call tools if the request is a general question answerable from the policy documents."
        )

    system_content = (
        f"{system_prompt}\n\n"
        "Use the policy information provided below to give accurate, specific answers. "
        "Quote specific numbers, deadlines, and procedures from the policies when relevant. "
        "If the policy documents don't cover the question, say so honestly.\n\n"
        "Respond helpfully in 2-3 sentences. "
        "Use the conversation history (if any) to understand context and "
        "avoid repeating information already provided."
        + ("\nSet needs_escalation to true with a reason if this needs manager approval."
           if can_escalate else "")
        + tool_instructions
    )

    prompt_template = ChatPromptTemplate.from_messages([
        SystemMessage(content=system_content),
        ("human",
         "{rag_context}\n\n"
         "{history}\n"
         "Employee: {employee_name}\nRequest: {request}"),
    ])

    # LLM with tools bound (for ReAct tool-calling phase)
    tool_llm = llm.bind_tools(domain_tools) if domain_tools else None

    def worker(state: SupportRequest) -> dict:
        ts = datetime.now().strftime("%H:%M:%S")
        try:
            messages = prompt_template.format_messages(
                rag_context=state.get("rag_context") or "",
                history=format_history(state),
                employee_name=state["employee_name"],
                request=state["request"],
            )

            # If this is a QA retry, inject the QA feedback so the worker can self-correct
            qa_feedback = state.get("qa_feedback") or ""
            if qa_feedback:
                messages.append(SystemMessage(content=(
                    f"Your previous response was rejected by quality review: {qa_feedback}\n"
                    "Please provide an improved response that addresses this feedback."
                )))

            tool_calls_log = []
            react_iterations = 0

            # === ReAct Loop: Think → Act → Observe ===
            if tool_llm and domain_tools:
                for iteration in range(MAX_TOOL_ITERATIONS):
                    react_iterations += 1
                    with trace_llm_call(f"{name}_react_iter_{iteration}") as ctx:
                        response = tool_llm.invoke(messages)
                        ctx["response"] = response

                    if not response.tool_calls:
                        # Agent decided it has enough info — move to final response
                        break

                    # ACT: Execute tool calls
                    messages.append(response)
                    tool_results = _execute_tool_calls(response, tools_by_name)
                    messages.extend(tool_results)

                    for tc in response.tool_calls:
                        tool_calls_log.append(f"{tc['name']}({tc['args']})")

                    # OBSERVE: Log what happened (LLM sees tool results on next iteration)
                else:
                    # Max iterations exhausted — add a nudge to wrap up
                    messages.append(SystemMessage(content=(
                        "You have reached the maximum number of tool calls. "
                        "Summarize what you found so far and provide the best answer you can."
                    )))

            # === Final structured response (with full tool context in messages) ===
            with trace_llm_call(f"{name}_worker_final") as ctx:
                result = worker_llm.invoke(messages)
                ctx["response"] = result

            escalate = can_escalate and result.needs_escalation
            reason = result.escalation_reason or "" if escalate else ""

            audit_parts = []
            if tool_calls_log:
                audit_parts.append(f"[{ts}] {name.title()} ReAct: {react_iterations} iteration(s), tools: {', '.join(tool_calls_log)}")
            if qa_feedback:
                audit_parts.append(f"[{ts}] {name.title()} self-correction after QA feedback")
            audit_parts.append(
                f"[{ts}] {name.title()} worker: {'escalating' if escalate else 'resolved'}"
            )

            return {
                "worker_output": result.response,
                "needs_escalation": escalate,
                "escalation_reason": reason,
                "tool_calls_made": tool_calls_log,
                "react_iterations": react_iterations,
                "error": "",
                "audit": audit_parts,
            }
        except Exception as e:
            return {"error": str(e), "audit": [f"[{ts}] {name.title()} worker error: {e}"]}

    worker.__name__ = f"{name}_worker"
    return worker


# Create domain workers from config
hr_worker = make_domain_worker("hr", **WORKER_CONFIGS["hr"])
tech_worker = make_domain_worker("tech", **WORKER_CONFIGS["tech"])
finance_worker = make_domain_worker("finance", **WORKER_CONFIGS["finance"])
facilities_worker = make_domain_worker("facilities", **WORKER_CONFIGS["facilities"])

# Map category → worker function for QA retry routing
_WORKER_FNS: dict = {
    "hr": hr_worker,
    "tech": tech_worker,
    "finance": finance_worker,
    "facilities": facilities_worker,
}


# --- Clarify & General (no LLM needed) ---

def clarify_agent(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    return {
        "worker_output": f"Hi {state['employee_name']}, I'm not sure I understand your request: "
                         f"'{state['request']}'. Could you provide more details?",
        "needs_escalation": False,
        "error": "",
        "audit": [f"[{ts}] Clarification requested (conf: {state['confidence']})"],
    }


def general_worker(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    return {
        "worker_output": f"Hi {state['employee_name']}, your request has been logged. "
                         f"A team member will get back to you shortly.",
        "needs_escalation": False,
        "error": "",
        "audit": [f"[{ts}] General worker"],
    }


# Register general_worker for QA retry
_WORKER_FNS["general"] = general_worker


# --- Escalation, QA, Fallback, Finalize ---

def escalation_check(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    if state["error"]:
        return {"audit": [f"[{ts}] Worker errored, sending to fallback"]}
    return {"audit": [f"[{ts}] Escalation check: {state['needs_escalation']}"]}


def route_escalation(state: SupportRequest) -> str:
    if state["error"]:
        return "fallback"
    if state["needs_escalation"]:
        return "manager"
    return "qa_check"


MANAGER_PROMPT = ChatPromptTemplate.from_messages([
    SystemMessage(content=(
        "You are a manager at UniGPS with authority over policy exceptions. "
        "Provide a definitive answer with your authority in 2-3 sentences. "
        "Use the policy information and conversation history to understand the full context."
    )),
    ("human",
     "{rag_context}\n\n"
     "{history}\n"
     "Escalation reason: {escalation_reason}\n"
     "Employee: {employee_name}\n"
     "Original request: {request}\n"
     "Worker's initial response: {worker_output}"),
])


def manager_agent(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    try:
        messages = MANAGER_PROMPT.format_messages(
            rag_context=state.get("rag_context") or "",
            history=format_history(state),
            escalation_reason=state["escalation_reason"],
            employee_name=state["employee_name"],
            request=state["request"],
            worker_output=state["worker_output"][:200],
        )
        with trace_llm_call("manager") as ctx:
            response = llm.invoke(messages)
            ctx["response"] = response
        return {
            "worker_output": f"[Manager Review] {response.content.strip()}",
            "error": "",
            "audit": [f"[{ts}] Manager resolved escalation"],
        }
    except Exception as e:
        return {"error": str(e), "audit": [f"[{ts}] Manager error: {e}"]}


# --- PII Detection Patterns ---

PII_PATTERNS = {
    "aadhaar": re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),                      # Indian Aadhaar
    "pan": re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),                              # Indian PAN
    "ssn": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),                               # US SSN
    "credit_card": re.compile(r"\b(?:\d{4}[- ]?){3}\d{4}\b"),                  # Credit card
    "phone_with_country": re.compile(r"\+\d{1,3}[-.\s]?\d{6,14}\b"),           # +91-9876543210
    "bank_account": re.compile(r"\b\d{9,18}\b"),                                # Bank account (long digits)
    "ifsc": re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b"),                           # IFSC code
}

# Terms that are OK in context (not PII even if they match digit patterns)
PII_WHITELIST = re.compile(
    r"(EXP-|TECH-|INR\s|Rs\.?\s|capacity|SLA|P[1-4]|port\s|ext\.\s|room\s|floor|"
    r"\d{4}-\d{2}-\d{2}|\d{1,2}:\d{2}|leave|days?\s|hours?\s|month)"
)


def _detect_pii(text: str) -> list[str]:
    """Scan text for PII patterns. Returns list of detected PII types."""
    detections = []
    for pii_type, pattern in PII_PATTERNS.items():
        matches = pattern.findall(text)
        for match in matches:
            # Skip if the match is in a whitelisted context
            start = text.find(match)
            context = text[max(0, start - 20):start + len(match) + 20]
            if PII_WHITELIST.search(context):
                continue
            detections.append(pii_type)
            break  # one detection per type is enough
    return detections


def _redact_pii(text: str) -> str:
    """Redact detected PII from text."""
    for pii_type, pattern in PII_PATTERNS.items():
        def _replace(match):
            ctx_start = max(0, match.start() - 20)
            context = text[ctx_start:match.end() + 20]
            if PII_WHITELIST.search(context):
                return match.group()
            return f"[REDACTED-{pii_type.upper()}]"
        text = pattern.sub(_replace, text)
    return text


def qa_check(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    output = state.get("worker_output") or ""
    issues = []

    # Content quality checks
    if not output.strip():
        issues.append("empty response")
    elif len(output) < 20:
        issues.append("response too short (less than 20 chars)")
    if "I don't know" in output and not state.get("rag_context"):
        issues.append("unhelpful response without attempting knowledge lookup")

    # PII guardrail
    pii_found = _detect_pii(output)
    redacted_output = output
    if pii_found:
        redacted_output = _redact_pii(output)
        issues.append(f"PII detected and redacted: {', '.join(pii_found)}")

    if issues:
        # If only PII was the issue, redact and pass (don't retry for PII)
        if pii_found and len(issues) == 1:
            return {
                "worker_output": redacted_output,
                "error": "",
                "qa_feedback": "",
                "audit": [f"[{ts}] QA PASS (PII redacted: {', '.join(pii_found)})"],
            }
        feedback = "; ".join(issues)
        return {
            "worker_output": redacted_output,
            "qa_feedback": feedback,
            "error": feedback,
            "audit": [f"[{ts}] QA FAIL: {feedback}"],
        }
    return {"error": "", "qa_feedback": "", "audit": [f"[{ts}] QA PASS"]}


def route_qa(state: SupportRequest) -> str:
    """Route QA results: retry worker once on failure, then fallback."""
    if not state.get("error"):
        return "finalize"
    retry_count = state.get("qa_retry_count") or 0
    if retry_count < MAX_QA_RETRIES:
        return "retry_worker"
    return "fallback"


def retry_worker(state: SupportRequest) -> dict:
    """Re-invoke the domain worker with QA feedback for self-correction."""
    ts = datetime.now().strftime("%H:%M:%S")
    category = state.get("category") or "general"
    retry_count = (state.get("qa_retry_count") or 0) + 1

    worker_fn = _WORKER_FNS.get(category)
    if not worker_fn:
        return {
            "qa_retry_count": retry_count,
            "error": f"No worker for category '{category}' to retry",
            "audit": [f"[{ts}] Retry failed: no worker for '{category}'"],
        }

    # Call the original worker — it reads qa_feedback from state for self-correction
    result = worker_fn(state)
    result["qa_retry_count"] = retry_count
    result["audit"] = [f"[{ts}] QA retry #{retry_count} for {category}"] + result.get("audit", [])
    return result


def fallback(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%H:%M:%S")
    fallback_templates = {
        "hr": "Please visit the HR portal or email hr@unigps.in.",
        "tech": "Please create a Jira ticket or contact IT at ext. 5555.",
        "finance": "Please email finance@unigps.in with your query.",
        "facilities": "Please email admin@unigps.in for facilities requests.",
        "general": "Your request has been noted. We'll respond shortly.",
    }
    output = fallback_templates.get(state["category"], fallback_templates["general"])
    return {
        "worker_output": output,
        "fallback_used": True,
        "error": "",
        "audit": [f"[{ts}] Fallback template: {state['category']}"],
    }


def finalize(state: SupportRequest) -> dict:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    fallback_note = " (via fallback)" if state.get("fallback_used") else ""

    # Build source citation line
    sources = state.get("rag_sources") or []
    source_line = ""
    if sources and not state.get("fallback_used"):
        source_line = f"\nSources: {', '.join(sources)}"

    return {
        "final_response": (
            f"[{state['category'].upper()}] {state['worker_output']}"
            f"{source_line}\n"
            f"— UniGPS Support{fallback_note} | {ts}"
        ),
        "audit": [f"[{ts}] Finalized for {state['employee_name']}"],
    }


# --- Build Graph ---

def build_graph():
    graph = StateGraph(SupportRequest)

    graph.add_node("supervisor", supervisor)
    graph.add_node("rag_retrieval", rag_retrieval)
    graph.add_node("clarify", clarify_agent)
    graph.add_node("hr_worker", hr_worker)
    graph.add_node("tech_worker", tech_worker)
    graph.add_node("finance_worker", finance_worker)
    graph.add_node("facilities_worker", facilities_worker)
    graph.add_node("general_worker", general_worker)
    graph.add_node("escalation_check", escalation_check)
    graph.add_node("manager", manager_agent)
    graph.add_node("qa_check", qa_check)
    graph.add_node("retry_worker", retry_worker)
    graph.add_node("fallback", fallback)
    graph.add_node("finalize", finalize)

    # Flow: START → supervisor → rag_retrieval → route to worker
    graph.add_edge(START, "supervisor")
    graph.add_conditional_edges("supervisor", route_supervisor, {
        "clarify": "clarify",
        "hr": "rag_retrieval",
        "tech": "rag_retrieval",
        "finance": "rag_retrieval",
        "facilities": "rag_retrieval",
        "general": "general_worker",
    })

    # After RAG retrieval, route to the correct worker based on category
    def route_to_worker(state: SupportRequest) -> str:
        return f"{state['category']}_worker"

    graph.add_conditional_edges("rag_retrieval", route_to_worker, {
        "hr_worker": "hr_worker",
        "tech_worker": "tech_worker",
        "finance_worker": "finance_worker",
        "facilities_worker": "facilities_worker",
    })

    graph.add_edge("clarify", "finalize")
    for w in ["hr_worker", "tech_worker", "finance_worker", "facilities_worker", "general_worker"]:
        graph.add_edge(w, "escalation_check")

    graph.add_conditional_edges("escalation_check", route_escalation, {
        "manager": "manager",
        "qa_check": "qa_check",
        "fallback": "fallback",
    })

    graph.add_edge("manager", "qa_check")

    graph.add_conditional_edges("qa_check", route_qa, {
        "finalize": "finalize",
        "retry_worker": "retry_worker",
        "fallback": "fallback",
    })

    # Retry worker goes back through escalation → QA for another check
    graph.add_edge("retry_worker", "escalation_check")

    graph.add_edge("fallback", "finalize")
    graph.add_edge("finalize", END)

    return graph
