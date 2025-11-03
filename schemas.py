"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field, HttpUrl
from typing import Optional, Dict, Any

# Example schemas (you can keep these as samples):

class User(BaseModel):
    """
    Users collection schema
    Collection name: "user" (lowercase of class name)
    """
    name: str = Field(..., description="Full name")
    email: str = Field(..., description="Email address")
    address: str = Field(..., description="Address")
    age: Optional[int] = Field(None, ge=0, le=120, description="Age in years")
    is_active: bool = Field(True, description="Whether user is active")

class Product(BaseModel):
    """
    Products collection schema
    Collection name: "product" (lowercase of class name)
    """
    title: str = Field(..., description="Product title")
    description: Optional[str] = Field(None, description="Product description")
    price: float = Field(..., ge=0, description="Price in dollars")
    category: str = Field(..., description="Product category")
    in_stock: bool = Field(True, description="Whether product is in stock")

# Agent Evaluator schemas

class EvaluationRequest(BaseModel):
    agent_card_url: HttpUrl = Field(..., description="URL to the agent card JSON or YAML")
    chat_url: Optional[HttpUrl] = Field(None, description="Optional URL to chat logs")

class Evaluation(BaseModel):
    """
    Evaluation documents
    Collection name: "evaluation"
    """
    agent_card_url: str
    chat_url: Optional[str] = None
    status: str = Field("queued", description="queued|running|completed|failed")
    metrics: Optional[Dict[str, Any]] = Field(None, description="Structured JSON metrics output")
    html_report: Optional[str] = Field(None, description="Pre-rendered HTML report")
    error: Optional[str] = Field(None, description="Error message if failed")
