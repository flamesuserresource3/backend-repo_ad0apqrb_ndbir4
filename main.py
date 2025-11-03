import os
import json
import time
from typing import Optional, Dict, Any
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from pydantic import ValidationError
import requests
from bson import ObjectId

from database import db
from schemas import EvaluationRequest, Evaluation

app = FastAPI(title="Agent Evaluator API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def fetch_with_retries(url: str, max_retries: int = 3, backoff: float = 0.8, timeout: int = 10) -> str:
    last_err: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, timeout=timeout)
            resp.raise_for_status()
            return resp.text
        except Exception as e:
            last_err = e
            if attempt < max_retries - 1:
                time.sleep(backoff * (2 ** attempt))
    raise HTTPException(status_code=502, detail=f"Failed to fetch {url}: {str(last_err)}")


def dummy_deepeval(agent_card: str, chat_logs: Optional[str]) -> Dict[str, Any]:
    """
    A lightweight, deterministic mock for deepeval.evaluate().
    It derives pseudo-scores from content lengths to keep things testable and reproducible.
    """
    base = max(1, len(agent_card))
    chat_factor = (len(chat_logs) if chat_logs else 0) % 1000

    def norm(v: float) -> float:
        return round(max(0.0, min(1.0, v)), 2)

    metrics = {
        "mcp_compliance": {
            "spec_alignment": norm((base % 100) / 100),
            "tools_schema_valid": norm(((base // 3) % 100) / 100),
            "errors": [],
        },
        "safety": {
            "toxicity": norm(((base + chat_factor) % 100) / 100),
            "compliance": norm(((base // 7 + chat_factor // 5) % 100) / 100),
            "harmfulness": norm(((base // 11) % 100) / 100),
        },
        "chatbot": {
            "relevance": norm(((base // 13 + chat_factor // 3) % 100) / 100),
            "helpfulness": norm(((base // 5) % 100) / 100),
            "factuality": norm(((base // 9) % 100) / 100),
            "latency": round(100 + (base % 50), 0),
        },
    }
    return metrics


def render_html_report(evaluation: Dict[str, Any]) -> str:
    m = evaluation.get("metrics", {})
    def row(label: str, value: Any) -> str:
        return f"<tr><td style='padding:8px;font-weight:600'>{label}</td><td style='padding:8px'>{value}</td></tr>"

    safety = m.get("safety", {})
    mcp = m.get("mcp_compliance", {})
    bot = m.get("chatbot", {})

    html = f"""
    <html>
      <head>
        <meta charset='utf-8' />
        <meta name='viewport' content='width=device-width, initial-scale=1' />
        <title>Agent Evaluator Report</title>
        <style>
          body {{ font-family: ui-sans-serif, system-ui, -apple-system; padding: 24px; background: #0b1020; color: #e6f0ff; }}
          .card {{ background: #0f172a; border: 1px solid #1f2a44; border-radius: 12px; padding: 20px; max-width: 960px; margin: 0 auto; }}
          h1 {{ font-size: 24px; margin: 0 0 12px; }}
          h2 {{ font-size: 18px; margin: 20px 0 8px; color: #9fb3ff; }}
          table {{ width: 100%; border-collapse: collapse; margin-top: 8px; }}
          tr:nth-child(even) td {{ background: #0b132b; }}
          td {{ border-top: 1px solid #1f2a44; }}
          .muted {{ color: #9fb3ff; }}
        </style>
      </head>
      <body>
        <div class='card'>
          <h1>Agent Evaluator Report</h1>
          <div class='muted'>Status: {evaluation.get('status')}</div>
          <div class='muted'>Agent Card: {evaluation.get('agent_card_url')}</div>
          <div class='muted'>Chat Logs: {evaluation.get('chat_url') or '—'}</div>

          <h2>MCP Compliance</h2>
          <table>
            {row('Spec Alignment', mcp.get('spec_alignment', 'n/a'))}
            {row('Tools Schema Valid', mcp.get('tools_schema_valid', 'n/a'))}
          </table>

          <h2>Safety</h2>
          <table>
            {row('Toxicity', safety.get('toxicity', 'n/a'))}
            {row('Compliance', safety.get('compliance', 'n/a'))}
            {row('Harmfulness', safety.get('harmfulness', 'n/a'))}
          </table>

          <h2>Chatbot Metrics</h2>
          <table>
            {row('Relevance', bot.get('relevance', 'n/a'))}
            {row('Helpfulness', bot.get('helpfulness', 'n/a'))}
            {row('Factuality', bot.get('factuality', 'n/a'))}
            {row('Latency (ms)', bot.get('latency', 'n/a'))}
          </table>
        </div>
      </body>
    </html>
    """
    return html


@app.get("/")
def read_root():
    return {"message": "Agent Evaluator API"}


@app.post("/evaluate")
def evaluate(req: EvaluationRequest):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")

    # Create initial record
    doc = Evaluation(
        agent_card_url=str(req.agent_card_url),
        chat_url=str(req.chat_url) if req.chat_url else None,
        status="running",
    ).model_dump()
    inserted = db["evaluation"].insert_one(doc)
    eval_id = str(inserted.inserted_id)

    try:
        agent_card_text = fetch_with_retries(str(req.agent_card_url))
        chat_text = fetch_with_retries(str(req.chat_url)) if req.chat_url else None

        metrics = dummy_deepeval(agent_card_text, chat_text)

        updated = {
            "status": "completed",
            "metrics": metrics,
        }
        # Render HTML after metrics populated
        temp_doc = {**doc, **updated}
        temp_doc["metrics"] = metrics
        temp_doc["status"] = "completed"
        temp_doc["agent_card_url"] = str(req.agent_card_url)
        temp_doc["chat_url"] = str(req.chat_url) if req.chat_url else None
        html = render_html_report(temp_doc)
        updated["html_report"] = html

        db["evaluation"].update_one({"_id": inserted.inserted_id}, {"$set": updated})

        return {"id": eval_id, "status": "completed", "metrics": metrics}
    except HTTPException:
        # Pass through HTTPExceptions as failure state
        db["evaluation"].update_one({"_id": inserted.inserted_id}, {"$set": {"status": "failed"}})
        raise
    except Exception as e:
        db["evaluation"].update_one({"_id": inserted.inserted_id}, {"$set": {"status": "failed", "error": str(e)}})
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")


@app.get("/evaluations/{evaluation_id}")
def get_evaluation(evaluation_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        _id = ObjectId(evaluation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid evaluation id")

    doc = db["evaluation"].find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/evaluations/{evaluation_id}/report", response_class=HTMLResponse)
def get_evaluation_report(evaluation_id: str):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        _id = ObjectId(evaluation_id)
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid evaluation id")

    doc = db["evaluation"].find_one({"_id": _id})
    if not doc:
        raise HTTPException(status_code=404, detail="Evaluation not found")
    html = doc.get("html_report")
    if not html:
        # If no pre-rendered HTML, render from stored metrics if available
        html = render_html_report({
            "status": doc.get("status"),
            "agent_card_url": doc.get("agent_card_url"),
            "chat_url": doc.get("chat_url"),
            "metrics": doc.get("metrics") or {},
        })
    return HTMLResponse(content=html, status_code=200)


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
