from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Set


@dataclass
class Group:
    group_id: str
    name: str
    limit_watts: Optional[int] = None
    appliances: Set[str] = field(default_factory=set)  # cp_ids
    subgroups: Set[str] = field(default_factory=set)   # group_ids


class GroupRegistry:
    """
    In-memory directed acyclic graph (DAG) of groups.
    Supports nested groups. Prevents cycles.
    """
    def __init__(self) -> None:
        self._groups: Dict[str, Group] = {}
        self._parents: Dict[str, Set[str]] = {}          # child_group_id -> parents
        self._appliance_groups: Dict[str, Set[str]] = {} # cp_id -> direct groups

    def list_groups(self) -> Dict[str, Group]:
        return self._groups

    def get_group(self, group_id: str) -> Optional[Group]:
        return self._groups.get(group_id)

    def create_group(self, group_id: str, name: str, limit_watts: Optional[int] = None) -> Group:
        if group_id in self._groups:
            raise ValueError("group already exists")
        g = Group(group_id=group_id, name=name, limit_watts=limit_watts)
        self._groups[group_id] = g
        self._parents.setdefault(group_id, set())
        return g

    def delete_group(self, group_id: str) -> None:
        g = self._groups.get(group_id)
        if not g:
            return

        # unlink from parents
        for parent in list(self._parents.get(group_id, set())):
            pg = self._groups.get(parent)
            if pg:
                pg.subgroups.discard(group_id)
        self._parents.pop(group_id, None)

        # unlink children
        for child in list(g.subgroups):
            self._parents.get(child, set()).discard(group_id)

        # unlink appliances reverse index
        for cp_id in list(g.appliances):
            self._appliance_groups.get(cp_id, set()).discard(group_id)

        self._groups.pop(group_id, None)

    def set_group_limit(self, group_id: str, limit_watts: Optional[int]) -> Group:
        g = self._require_group(group_id)
        g.limit_watts = limit_watts
        return g

    def add_appliance(self, group_id: str, cp_id: str) -> Group:
        g = self._require_group(group_id)
        g.appliances.add(cp_id)
        self._appliance_groups.setdefault(cp_id, set()).add(group_id)
        return g

    def remove_appliance(self, group_id: str, cp_id: str) -> Group:
        g = self._require_group(group_id)
        g.appliances.discard(cp_id)
        self._appliance_groups.get(cp_id, set()).discard(group_id)
        return g

    def add_subgroup(self, parent_group_id: str, child_group_id: str) -> Group:
        parent = self._require_group(parent_group_id)
        self._require_group(child_group_id)

        if parent_group_id == child_group_id:
            raise ValueError("cannot add group as subgroup of itself")

        # cycle check: parent must not be reachable from child
        if self._reachable(child_group_id, parent_group_id):
            raise ValueError("would create cycle")

        parent.subgroups.add(child_group_id)
        self._parents.setdefault(child_group_id, set()).add(parent_group_id)
        return parent

    def remove_subgroup(self, parent_group_id: str, child_group_id: str) -> Group:
        parent = self._require_group(parent_group_id)
        parent.subgroups.discard(child_group_id)
        self._parents.get(child_group_id, set()).discard(parent_group_id)
        return parent

    def appliance_groups(self, cp_id: str) -> Set[str]:
        return set(self._appliance_groups.get(cp_id, set()))

    def ancestors_of_group(self, group_id: str) -> Set[str]:
        visited: Set[str] = set()
        stack = list(self._parents.get(group_id, set()))
        while stack:
            p = stack.pop()
            if p in visited:
                continue
            visited.add(p)
            stack.extend(list(self._parents.get(p, set())))
        return visited

    def ancestors_of_appliance(self, cp_id: str) -> Set[str]:
        direct = self._appliance_groups.get(cp_id, set())
        all_groups: Set[str] = set(direct)
        for g in list(direct):
            all_groups |= self.ancestors_of_group(g)
        return all_groups

    def _require_group(self, group_id: str) -> Group:
        g = self._groups.get(group_id)
        if not g:
            raise ValueError("group not found")
        return g

    def _reachable(self, start_group_id: str, target_group_id: str) -> bool:
        visited: Set[str] = set()
        stack = [start_group_id]
        while stack:
            cur = stack.pop()
            if cur == target_group_id:
                return True
            if cur in visited:
                continue
            visited.add(cur)
            g = self._groups.get(cur)
            if not g:
                continue
            stack.extend(list(g.subgroups))
        return False


GROUPS = GroupRegistry()
