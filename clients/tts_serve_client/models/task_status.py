from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast

if TYPE_CHECKING:
  from ..models.task_status_options import TaskStatusOptions





T = TypeVar("T", bound="TaskStatus")



@_attrs_define
class TaskStatus:
    """ 
        Attributes:
            task_id (str):
            status (str):
            stage (None | str | Unset):
            client_id (None | str | Unset):
            source_type (None | str | Unset):
            error (None | str | Unset):
            created_at (float | None | Unset):
            updated_at (float | None | Unset):
            options (TaskStatusOptions | Unset):
     """

    task_id: str
    status: str
    stage: None | str | Unset = UNSET
    client_id: None | str | Unset = UNSET
    source_type: None | str | Unset = UNSET
    error: None | str | Unset = UNSET
    created_at: float | None | Unset = UNSET
    updated_at: float | None | Unset = UNSET
    options: TaskStatusOptions | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.task_status_options import TaskStatusOptions
        task_id = self.task_id

        status = self.status

        stage: None | str | Unset
        if isinstance(self.stage, Unset):
            stage = UNSET
        else:
            stage = self.stage

        client_id: None | str | Unset
        if isinstance(self.client_id, Unset):
            client_id = UNSET
        else:
            client_id = self.client_id

        source_type: None | str | Unset
        if isinstance(self.source_type, Unset):
            source_type = UNSET
        else:
            source_type = self.source_type

        error: None | str | Unset
        if isinstance(self.error, Unset):
            error = UNSET
        else:
            error = self.error

        created_at: float | None | Unset
        if isinstance(self.created_at, Unset):
            created_at = UNSET
        else:
            created_at = self.created_at

        updated_at: float | None | Unset
        if isinstance(self.updated_at, Unset):
            updated_at = UNSET
        else:
            updated_at = self.updated_at

        options: dict[str, Any] | Unset = UNSET
        if not isinstance(self.options, Unset):
            options = self.options.to_dict()


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "task_id": task_id,
            "status": status,
        })
        if stage is not UNSET:
            field_dict["stage"] = stage
        if client_id is not UNSET:
            field_dict["client_id"] = client_id
        if source_type is not UNSET:
            field_dict["source_type"] = source_type
        if error is not UNSET:
            field_dict["error"] = error
        if created_at is not UNSET:
            field_dict["created_at"] = created_at
        if updated_at is not UNSET:
            field_dict["updated_at"] = updated_at
        if options is not UNSET:
            field_dict["options"] = options

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.task_status_options import TaskStatusOptions
        d = dict(src_dict)
        task_id = d.pop("task_id")

        status = d.pop("status")

        def _parse_stage(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        stage = _parse_stage(d.pop("stage", UNSET))


        def _parse_client_id(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        client_id = _parse_client_id(d.pop("client_id", UNSET))


        def _parse_source_type(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        source_type = _parse_source_type(d.pop("source_type", UNSET))


        def _parse_error(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        error = _parse_error(d.pop("error", UNSET))


        def _parse_created_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        created_at = _parse_created_at(d.pop("created_at", UNSET))


        def _parse_updated_at(data: object) -> float | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(float | None | Unset, data)

        updated_at = _parse_updated_at(d.pop("updated_at", UNSET))


        _options = d.pop("options", UNSET)
        options: TaskStatusOptions | Unset
        if isinstance(_options,  Unset):
            options = UNSET
        else:
            options = TaskStatusOptions.from_dict(_options)




        task_status = cls(
            task_id=task_id,
            status=status,
            stage=stage,
            client_id=client_id,
            source_type=source_type,
            error=error,
            created_at=created_at,
            updated_at=updated_at,
            options=options,
        )


        task_status.additional_properties = d
        return task_status

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
