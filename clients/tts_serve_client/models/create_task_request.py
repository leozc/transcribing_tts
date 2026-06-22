from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="CreateTaskRequest")



@_attrs_define
class CreateTaskRequest:
    """ 
        Attributes:
            source (str):
            client_id (str):
            hotwords (None | str | Unset):
            speakers (int | None | Unset):
            reid (bool | Unset):  Default: False.
            names (bool | Unset):  Default: False.
            clip (None | str | Unset):
            name (None | str | Unset):
     """

    source: str
    client_id: str
    hotwords: None | str | Unset = UNSET
    speakers: int | None | Unset = UNSET
    reid: bool | Unset = False
    names: bool | Unset = False
    clip: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        source = self.source

        client_id = self.client_id

        hotwords: None | str | Unset
        if isinstance(self.hotwords, Unset):
            hotwords = UNSET
        else:
            hotwords = self.hotwords

        speakers: int | None | Unset
        if isinstance(self.speakers, Unset):
            speakers = UNSET
        else:
            speakers = self.speakers

        reid = self.reid

        names = self.names

        clip: None | str | Unset
        if isinstance(self.clip, Unset):
            clip = UNSET
        else:
            clip = self.clip

        name: None | str | Unset
        if isinstance(self.name, Unset):
            name = UNSET
        else:
            name = self.name


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
            "source": source,
            "client_id": client_id,
        })
        if hotwords is not UNSET:
            field_dict["hotwords"] = hotwords
        if speakers is not UNSET:
            field_dict["speakers"] = speakers
        if reid is not UNSET:
            field_dict["reid"] = reid
        if names is not UNSET:
            field_dict["names"] = names
        if clip is not UNSET:
            field_dict["clip"] = clip
        if name is not UNSET:
            field_dict["name"] = name

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        source = d.pop("source")

        client_id = d.pop("client_id")

        def _parse_hotwords(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        hotwords = _parse_hotwords(d.pop("hotwords", UNSET))


        def _parse_speakers(data: object) -> int | None | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(int | None | Unset, data)

        speakers = _parse_speakers(d.pop("speakers", UNSET))


        reid = d.pop("reid", UNSET)

        names = d.pop("names", UNSET)

        def _parse_clip(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        clip = _parse_clip(d.pop("clip", UNSET))


        def _parse_name(data: object) -> None | str | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            return cast(None | str | Unset, data)

        name = _parse_name(d.pop("name", UNSET))


        create_task_request = cls(
            source=source,
            client_id=client_id,
            hotwords=hotwords,
            speakers=speakers,
            reid=reid,
            names=names,
            clip=clip,
            name=name,
        )


        create_task_request.additional_properties = d
        return create_task_request

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
