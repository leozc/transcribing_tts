from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TypeVar, BinaryIO, TextIO, TYPE_CHECKING, Generator

from attrs import define as _attrs_define
from attrs import field as _attrs_field

from ..types import UNSET, Unset

from ..types import UNSET, Unset
from typing import cast

if TYPE_CHECKING:
  from ..models.queue_status_counts import QueueStatusCounts
  from ..models.queued_item import QueuedItem
  from ..models.running_info import RunningInfo





T = TypeVar("T", bound="QueueStatus")



@_attrs_define
class QueueStatus:
    """ 
        Attributes:
            running (None | RunningInfo | Unset):
            queued (list[QueuedItem] | Unset):
            counts (QueueStatusCounts | Unset):
     """

    running: None | RunningInfo | Unset = UNSET
    queued: list[QueuedItem] | Unset = UNSET
    counts: QueueStatusCounts | Unset = UNSET
    additional_properties: dict[str, Any] = _attrs_field(init=False, factory=dict)





    def to_dict(self) -> dict[str, Any]:
        from ..models.queue_status_counts import QueueStatusCounts
        from ..models.queued_item import QueuedItem
        from ..models.running_info import RunningInfo
        running: dict[str, Any] | None | Unset
        if isinstance(self.running, Unset):
            running = UNSET
        elif isinstance(self.running, RunningInfo):
            running = self.running.to_dict()
        else:
            running = self.running

        queued: list[dict[str, Any]] | Unset = UNSET
        if not isinstance(self.queued, Unset):
            queued = []
            for queued_item_data in self.queued:
                queued_item = queued_item_data.to_dict()
                queued.append(queued_item)



        counts: dict[str, Any] | Unset = UNSET
        if not isinstance(self.counts, Unset):
            counts = self.counts.to_dict()


        field_dict: dict[str, Any] = {}
        field_dict.update(self.additional_properties)
        field_dict.update({
        })
        if running is not UNSET:
            field_dict["running"] = running
        if queued is not UNSET:
            field_dict["queued"] = queued
        if counts is not UNSET:
            field_dict["counts"] = counts

        return field_dict



    @classmethod
    def from_dict(cls: type[T], src_dict: Mapping[str, Any]) -> T:
        from ..models.queue_status_counts import QueueStatusCounts
        from ..models.queued_item import QueuedItem
        from ..models.running_info import RunningInfo
        d = dict(src_dict)
        def _parse_running(data: object) -> None | RunningInfo | Unset:
            if data is None:
                return data
            if isinstance(data, Unset):
                return data
            try:
                if not isinstance(data, dict):
                    raise TypeError()
                running_type_0 = RunningInfo.from_dict(data)



                return running_type_0
            except (TypeError, ValueError, AttributeError, KeyError):
                pass
            return cast(None | RunningInfo | Unset, data)

        running = _parse_running(d.pop("running", UNSET))


        _queued = d.pop("queued", UNSET)
        queued: list[QueuedItem] | Unset = UNSET
        if _queued is not UNSET:
            queued = []
            for queued_item_data in _queued:
                queued_item = QueuedItem.from_dict(queued_item_data)



                queued.append(queued_item)


        _counts = d.pop("counts", UNSET)
        counts: QueueStatusCounts | Unset
        if isinstance(_counts,  Unset):
            counts = UNSET
        else:
            counts = QueueStatusCounts.from_dict(_counts)




        queue_status = cls(
            running=running,
            queued=queued,
            counts=counts,
        )


        queue_status.additional_properties = d
        return queue_status

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
