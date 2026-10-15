"""Which WhatsApp groups are volunteering groups.

Most groups feed the school calendar. A volunteering group is one where parents arrange who helps when
and what the helping involves (e.g. "Preschool P.E. Teachers"). Each one gets its own volunteering calendar.

A group can come with its *usual routine* ("classes are usually on Fridays at 11:30am"). Parents lean on it
("it's me this week" means this week's class), so it is given to Claude to fill in what a message leaves out.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

DEFAULT_VOLUNTEERING_GROUPS = {
    "Preschool P.E. Teachers": "The preschool P.E. class is usually on Fridays at 11:30am.",
}


def _key(name: str) -> str:
    return " ".join(name.split()).casefold()


def parse_groups(text: str) -> dict[str, str]:
    """Reads "Name=usual routine; Other Name" into {name: routine}. The routine is optional."""
    groups: dict[str, str] = {}
    for entry in text.split(";"):
        name, _, routine = entry.partition("=")
        if name.strip():
            groups[name.strip()] = routine.strip()
    return groups


class VolunteeringGroups:
    """The configured volunteering groups, matched by name ignoring case and extra spaces."""

    def __init__(self, groups: Iterable[str] | Mapping[str, str] = ()):
        routines = groups if isinstance(groups, Mapping) else dict.fromkeys(groups, "")
        by_key: dict[str, tuple[str, str]] = {}
        for name, routine in routines.items():
            name = " ".join(name.split())
            if name:
                by_key.setdefault(_key(name), (name, routine.strip()))
        self._by_key = by_key

    def calendar_for(self, group: str) -> str | None:
        """The volunteering calendar for a group name, or None if it's an ordinary group."""
        found = self._by_key.get(_key(group)) if group else None
        return found[0] if found else None

    def routine_for(self, group: str) -> str:
        """What's usually true of this group's volunteering (day, time, ...), or "" if nothing was given."""
        found = self._by_key.get(_key(group)) if group else None
        return found[1] if found else ""

    def __iter__(self) -> Iterator[str]:
        return (name for name, _ in self._by_key.values())

    def __len__(self) -> int:
        return len(self._by_key)
