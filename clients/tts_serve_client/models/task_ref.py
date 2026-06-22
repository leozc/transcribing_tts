from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="TaskRef")



@_attrs_define
class TaskRef:
    """ 
        Attributes:
            task_id (str):
            status (str):
            pull_token (None | str | Unset):
     """

    task_id: str
    status: str
    pull_token: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        task_id = self.task_id

        status = self.status

        pull_token: None | str | Unset
        if isinstance(self.pull_token, Unset):
            pull_token = UNSET
        else:
            pull_token = self.pull_token


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "task_id": task_id,
            "status": status,
        })
        if pull_token is not UNSET:
            field_dict["pull_token"] = pull_token

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        task_id = d.pop("task_id")

        status = d.pop("status")

        def _parse_pull_token(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        pull_token = _parse_pull_token(d.pop("pull_token", UNSET))


        task_ref = cls(
            task_id=task_id,
            status=status,
            pull_token=pull_token,
        )


        task_ref.additional_properties = d
        return task_ref

    @property
    def additional_keys(self) -> list[str]:
        return list(self.additional_properties.keys())

    def __getitem__(self, key: str) -> Any:
        return self.additional_properties[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.additional_properties[key] = value

    def __delitem__(self, key: str) -> None:
        del self.additional_properties[key]

    def __contains__(self, key: str) -> bool:
        return key in self.additional_properties
