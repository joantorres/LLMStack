import uuid
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel

from llmstack.common.blocks.base.schema import StrEnum


class MessageType(StrEnum):
    BEGIN = "BEGIN"
    CONTENT = "CONTENT"
    CONTENT_STREAM_CHUNK = "CONTENT_STREAM_CHUNK"
    CONTENT_STREAM_BEGIN = "CONTENT_STREAM_BEGIN"
    CONTENT_STREAM_END = "CONTENT_STREAM_END"
    ERRORS = "ERRORS"


class MessageData(BaseModel):
    pass


class Error(BaseModel):
    code: int = -1
    message: str


class ErrorsData(MessageData):
    errors: List[Error]


class ContentData(MessageData):
    content: Any


class ContentStreamChunkData(MessageData):
    chunk: Any


class Message(BaseModel):
    id: str = str(uuid.uuid4())
    type: MessageType
    sender: str
    receiver: Optional[str] = None
    reply_to: Optional[str] = None  # Id of the message this is a reply to
    data: Optional[
        Union[
            ContentData,
            ContentStreamChunkData,
            ErrorsData,
            Dict,
        ]
    ] = None
