from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class RegisterIn(BaseModel):
    username: str = Field(min_length=3, max_length=50)
    password: str = Field(min_length=6, max_length=64)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str
    nickname: str | None
    avatar: str | None


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class ErDiagramIn(BaseModel):
    ddl: str = Field(min_length=1, max_length=20000)


class MermaidIn(BaseModel):
    text: str = Field(min_length=1, max_length=10000)


class DiagramIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    content: str = Field(min_length=1, max_length=500000)  # drawio XML 可能较大


class DiagramSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    update_time: datetime


class DiagramOut(DiagramSummary):
    content: str
