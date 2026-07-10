from pydantic import BaseModel, ConfigDict


class BaseConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        arbitrary_types_allowed=True,
    )
