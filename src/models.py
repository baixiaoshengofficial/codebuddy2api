"""
Data models for CodeBuddy2API
"""
from typing import List, Optional, Dict, Any, Union
from pydantic import BaseModel, Field


class AuthorizationPollCommand(BaseModel):
    auth_state: Optional[str] = Field(None, min_length=1, max_length=2048)


class CredentialDeleteCommand(BaseModel):
    # Legacy index-only requests are rejected explicitly by the HTTP boundary.
    filename: Optional[str] = Field(None, min_length=1, max_length=255)


class Message(BaseModel):
    role: str
    content: Union[str, List[Dict[str, Any]]]  # Support both string and complex list format


class ChatCompletionRequest(BaseModel):
    model: str
    messages: List[Message]
    stream: bool = False
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = 1.0
    frequency_penalty: Optional[float] = 0.0
    presence_penalty: Optional[float] = 0.0
    stop: Optional[Union[str, List[str]]] = None
    tools: Optional[List[Dict[str, Any]]] = None
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None


class ChatCompletionChoice(BaseModel):
    index: int
    message: Message
    finish_reason: Optional[str] = None


class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionChoice]
    usage: Optional[Dict[str, int]] = None


class Model(BaseModel):
    id: str
    object: str = "model"
    created: int = 0
    owned_by: str = "codebuddy"


class ModelList(BaseModel):
    object: str = "list"
    data: List[Model]


class CredentialInfo(BaseModel):
    """凭证信息"""
    index: int
    user_id: str
    created_at: int
    has_token: bool
