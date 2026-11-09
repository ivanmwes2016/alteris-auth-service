"""
A student's NFC card. camelCase on the wire, like the registers that read it.
Phones report a chip's serial number differently ("04:a2:..", "04A2.."), so it's
stored in one form: upper-case hex with no separators.
"""

import re
from datetime import datetime
from typing import Annotated

from pydantic import AfterValidator, BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

# NFC-A serials are 4, 7 or 10 bytes.
_SERIAL_LENGTHS = frozenset({8, 14, 20})


def normalise_uid(value: str) -> str:
    uid = re.sub(r"[\s:-]", "", value).upper()
    if len(uid) not in _SERIAL_LENGTHS or not re.fullmatch(r"[0-9A-F]+", uid):
        raise ValueError("That doesn't look like a card's serial number")
    return uid


CardUid = Annotated[str, AfterValidator(normalise_uid)]


class _Camel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, from_attributes=True)


class CardLink(_Camel):
    uid: CardUid


class CardRead(_Camel):
    uid: str
    linked_at: datetime
