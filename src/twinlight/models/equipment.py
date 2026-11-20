"""T-API v2.6.0 physical inventory (``tapi-equipment.yang``).

``tapi-equipment`` augments the context with ``physical-context``: a list of
``device`` (each holding ``equipment`` and ``access-port``) and a list of
``physical-span``. There is no ``equipment-context`` in v2.6.0.

The twin's inventory is its GNPy elements:

* every ROADM, transceiver and amplifier is a ``device`` holding one
  ``equipment``; the GNPy kind is the equipment's ``equipment-type-name`` and
  its ``type_variety`` (e.g. ``std_medium_gain``) the
  ``equipment-type-identifier``, both under
  ``actual-equipment/common-equipment-properties``;
* every fiber is a ``physical-span``.

UUIDs are derived from the element UID (uuid5), and the UID itself is kept in
each object's ``name`` list as ``element-uid``. Element attributes the
standard has no leaf for — a fiber's length, for one — are served by the
transparent controller (``/transparent/data/ElementList``), not here.

Every model here is frozen and holds its lists as tuples:
``TapiContext.physical_context()`` builds the tree once and hands the same
object to every request, so no caller may be able to change it. A caller
that needs to change something works on its own ``model_dump``.
"""

from __future__ import annotations

import uuid as uuid_lib

from pydantic import BaseModel, ConfigDict, Field, SerializerFunctionWrapHandler, model_serializer

from twinlight.models.common import NameAndValue


def element_uuid(uid: str, role: str) -> str:
    """Stable UUID of an element's ``role`` object (device/equipment/span)."""
    return str(uuid_lib.uuid5(uuid_lib.NAMESPACE_DNS, f"{uid}:{role}"))


class ElementName(NameAndValue):
    """A ``name`` entry of the tree: ``NameAndValue``, frozen."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)


class _OmitNone(BaseModel):
    """Absent optional leaves are left out rather than sent as JSON null."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    @model_serializer(mode="wrap")
    def _omit_none(self, handler: SerializerFunctionWrapHandler) -> dict:
        data: dict = handler(self)
        return {k: v for k, v in data.items() if v is not None}


class CommonEquipmentProperties(_OmitNone):
    """``common-equipment-properties`` (the subset the twin knows)."""

    equipment_type_name: str = Field(..., alias="equipment-type-name")
    equipment_type_identifier: str | None = Field(
        default=None, alias="equipment-type-identifier"
    )


class ActualEquipment(BaseModel):
    """``actual-equipment``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    common_equipment_properties: CommonEquipmentProperties = Field(
        ..., alias="common-equipment-properties"
    )


class Equipment(BaseModel):
    """``tapi-equipment:equipment``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    uuid: str
    name: tuple[ElementName, ...] = ()
    actual_equipment: ActualEquipment = Field(..., alias="actual-equipment")


class Device(BaseModel):
    """``tapi-equipment:device``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    uuid: str
    name: tuple[ElementName, ...] = ()
    equipment: tuple[Equipment, ...] = ()


class PhysicalSpan(BaseModel):
    """``tapi-equipment:physical-span``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    uuid: str
    name: tuple[ElementName, ...] = ()


class PhysicalContext(BaseModel):
    """``tapi-equipment:physical-context``."""

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    device: tuple[Device, ...] = ()
    physical_span: tuple[PhysicalSpan, ...] = Field(default=(), alias="physical-span")
