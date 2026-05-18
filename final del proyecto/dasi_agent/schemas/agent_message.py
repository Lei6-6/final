from pydantic import BaseModel


class AgentMessage(BaseModel):
    msg: str


class SendMessage(BaseModel):
    message: str
    alias: str