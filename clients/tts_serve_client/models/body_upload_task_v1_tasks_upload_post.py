from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field
import json
from .. import types

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast






T = TypeVar("T", bound="BodyUploadTaskV1TasksUploadPost")



@_attrs_define
class BodyUploadTaskV1TasksUploadPost:
    """ 
        Attributes:
            file (str):
            hotwords (None | str | Unset):
            speakers (int | None | Unset):
            reid (bool | Unset):  Default: False.
            names (bool | Unset):  Default: False.
            clip (None | str | Unset):
            name (None | str | Unset):
     """

    file: str
    hotwords: None | str | Unset = UNSET
    speakers: int | None | Unset = UNSET
    reid: bool | Unset = False
    names: bool | Unset = False
    clip: None | str | Unset = UNSET
    name: None | str | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        file = self.file

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
            "file": file,
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


    def to_multipart(self) -> types.RequestFiles:
        files: types.RequestFiles = []

        files.append(("file", (None, str(self.file).encode(), "text/plain")))



        if not isinstance(self.hotwords, Unset):
            if isinstance(self.hotwords, str):

                files.append(("hotwords", (None, str(self.hotwords).encode(), "text/plain")))
            else:
                files.append(("hotwords", (None, str(self.hotwords).encode(), "text/plain")))


        if not isinstance(self.speakers, Unset):
            if isinstance(self.speakers, int):

                files.append(("speakers", (None, str(self.speakers).encode(), "text/plain")))
            else:
                files.append(("speakers", (None, str(self.speakers).encode(), "text/plain")))


        if not isinstance(self.reid, Unset):
            files.append(("reid", (None, str(self.reid).encode(), "text/plain")))



        if not isinstance(self.names, Unset):
            files.append(("names", (None, str(self.names).encode(), "text/plain")))



        if not isinstance(self.clip, Unset):
            if isinstance(self.clip, str):

                files.append(("clip", (None, str(self.clip).encode(), "text/plain")))
            else:
                files.append(("clip", (None, str(self.clip).encode(), "text/plain")))


        if not isinstance(self.name, Unset):
            if isinstance(self.name, str):

                files.append(("name", (None, str(self.name).encode(), "text/plain")))
            else:
                files.append(("name", (None, str(self.name).encode(), "text/plain")))



        for prop_name, prop in self.additional_properties.items():
            files.append((prop_name, (None, str(prop).encode(), "text/plain")))



        return files


    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        d = dict(src_dict)
        file = d.pop("file")

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


        body_upload_task_v1_tasks_upload_post = cls(
            file=file,
            hotwords=hotwords,
            speakers=speakers,
            reid=reid,
            names=names,
            clip=clip,
            name=name,
        )


        body_upload_task_v1_tasks_upload_post.additional_properties = d
        return body_upload_task_v1_tasks_upload_post

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
