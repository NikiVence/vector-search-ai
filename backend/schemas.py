from pydantic import BaseModel, Field, field_validator

class CollectionCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @field_validator("name", mode="before")
    @classmethod
    def strip_name(cls, value):
        if isinstance(value, str):
            return value.strip()
        return value