"""Attribute-Based Access Control (ABAC) Policy domain model.

Safe, deterministic, framework-independent policy representation with
typed conditions and AST evaluation. No eval() or dynamic execution.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from shared.domain.errors import InvalidValue
from shared.domain.identifiers import PolicyId, TenantId, new_id


class PolicyEffect(StrEnum):
    """Effect applied when policy conditions evaluate to True."""

    ALLOW = "ALLOW"
    DENY = "DENY"


class PolicyStatus(StrEnum):
    """Operational lifecycle state of a policy."""

    ACTIVE = "ACTIVE"
    INACTIVE = "INACTIVE"


class ConditionOperator(StrEnum):
    """Safe comparison operators supported in ABAC rules."""

    EQUALS = "eq"
    NOT_EQUALS = "neq"
    IN = "in"
    NOT_IN = "not_in"
    LESS_THAN = "lt"
    LESS_THAN_OR_EQUAL = "lte"
    GREATER_THAN = "gt"
    GREATER_THAN_OR_EQUAL = "gte"
    CONTAINS = "contains"
    EXISTS = "exists"
    # Combinators
    AND = "and"
    OR = "or"
    NOT = "not"


def evaluate_condition(
    condition: dict[str, Any] | None,
    context_data: dict[str, Any],
) -> bool:
    """Safely and deterministically evaluate a condition dict against context data.

    context_data provides resolved attributes under prefixes:
      'subject': subject attributes
      'resource': resource attributes
      'action': action identifier
      'environment': environment attributes

    Returns False if any required attribute is missing or comparison is invalid.
    """
    if not condition:
        # Empty condition evaluates to True (unconditional match)
        return True

    op_str = condition.get("operator")
    if not op_str:
        raise InvalidValue(
            "condition dictionary missing 'operator'", details={"condition": condition}
        )

    try:
        operator = ConditionOperator(op_str)
    except ValueError as exc:
        raise InvalidValue(
            f"unsupported condition operator: {op_str}", details={"operator": op_str}
        ) from exc

    # Combinator operators
    if operator is ConditionOperator.AND:
        clauses = condition.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            raise InvalidValue(
                "AND condition requires non-empty list 'clauses'", details={"condition": condition}
            )
        return all(evaluate_condition(c, context_data) for c in clauses)

    if operator is ConditionOperator.OR:
        clauses = condition.get("clauses")
        if not isinstance(clauses, list) or not clauses:
            raise InvalidValue(
                "OR condition requires non-empty list 'clauses'", details={"condition": condition}
            )
        return any(evaluate_condition(c, context_data) for c in clauses)

    if operator is ConditionOperator.NOT:
        clause = condition.get("clause")
        if not isinstance(clause, dict):
            raise InvalidValue(
                "NOT condition requires dictionary 'clause'", details={"condition": condition}
            )
        return not evaluate_condition(clause, context_data)

    # Leaf comparison operators
    field_path = condition.get("field")
    if not isinstance(field_path, str) or not field_path:
        raise InvalidValue(
            "comparison condition requires string 'field'", details={"condition": condition}
        )

    actual_value, exists = _resolve_path(field_path, context_data)

    if operator is ConditionOperator.EXISTS:
        return exists

    if not exists:
        # Missing attribute safely evaluates to False
        return False

    if "value_field" in condition:
        val_path = condition["value_field"]
        if isinstance(val_path, str):
            expected_value, v_exists = _resolve_path(val_path, context_data)
            if not v_exists:
                return False
        else:
            return False
    else:
        expected_value = condition.get("value")

    if operator is ConditionOperator.EQUALS:
        return bool(actual_value == expected_value)

    if operator is ConditionOperator.NOT_EQUALS:
        return bool(actual_value != expected_value)

    if operator is ConditionOperator.IN:
        if not isinstance(expected_value, (list, tuple, set, frozenset)):
            return False
        return actual_value in expected_value

    if operator is ConditionOperator.NOT_IN:
        if not isinstance(expected_value, (list, tuple, set, frozenset)):
            return False
        return actual_value not in expected_value

    if operator is ConditionOperator.CONTAINS:
        if not isinstance(actual_value, (list, tuple, set, frozenset, str)):
            return False
        return expected_value in actual_value

    try:
        if operator is ConditionOperator.LESS_THAN:
            return bool(actual_value < expected_value)
        if operator is ConditionOperator.LESS_THAN_OR_EQUAL:
            return bool(actual_value <= expected_value)
        if operator is ConditionOperator.GREATER_THAN:
            return bool(actual_value > expected_value)
        return bool(actual_value >= expected_value)
    except TypeError:
        return False


def _resolve_path(path: str, data: dict[str, Any]) -> tuple[Any, bool]:
    """Resolve dot-notated field path e.g. 'resource.warehouse_id'.

    Returns (value, exists).
    """
    parts = path.split(".")
    curr: Any = data
    for part in parts:
        if isinstance(curr, dict) and part in curr:
            curr = curr[part]
        else:
            return None, False
    return curr, True


def validate_condition_ast(condition: dict[str, Any] | None) -> None:
    """Validate structure and operators of a condition dictionary."""
    if condition is None:
        return
    if not isinstance(condition, dict):
        raise InvalidValue("condition must be a dictionary", details={"condition": condition})

    # Evaluate against empty mock context to check operator and structure validity
    evaluate_condition(condition, {"subject": {}, "resource": {}, "environment": {}})


class AccessPolicy:
    """ABAC Access Policy aggregate."""

    __slots__ = (
        "_actions",
        "_conditions",
        "_created_at",
        "_description",
        "_effect",
        "_id",
        "_name",
        "_resources",
        "_status",
        "_tenant_id",
        "_updated_at",
        "_version",
    )

    def __init__(
        self,
        *,
        id: PolicyId,
        tenant_id: TenantId | None,
        name: str,
        description: str,
        effect: PolicyEffect,
        actions: Sequence[str],
        resources: Sequence[str],
        conditions: dict[str, Any] | None,
        status: PolicyStatus,
        created_at: datetime,
        updated_at: datetime,
        version: int = 1,
    ) -> None:
        self._id = id
        self._tenant_id = tenant_id
        self._name = _validate_policy_name(name)
        self._description = description.strip()
        self._effect = effect
        self._actions = tuple(actions)
        self._resources = tuple(resources)
        validate_condition_ast(conditions)
        self._conditions = conditions
        self._status = status
        self._created_at = created_at
        self._updated_at = updated_at
        self._version = max(1, version)

    @classmethod
    def create(
        cls,
        *,
        name: str,
        description: str,
        effect: PolicyEffect,
        actions: Sequence[str],
        resources: Sequence[str],
        now: datetime,
        conditions: dict[str, Any] | None = None,
        tenant_id: TenantId | None = None,
        status: PolicyStatus = PolicyStatus.ACTIVE,
        id: PolicyId | None = None,
    ) -> AccessPolicy:
        policy_id = id or PolicyId(new_id())
        return cls(
            id=policy_id,
            tenant_id=tenant_id,
            name=name,
            description=description,
            effect=effect,
            actions=actions,
            resources=resources,
            conditions=conditions,
            status=status,
            created_at=now,
            updated_at=now,
            version=1,
        )

    # -- properties --------------------------------------------------------
    @property
    def id(self) -> PolicyId:
        return self._id

    @property
    def tenant_id(self) -> TenantId | None:
        return self._tenant_id

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._description

    @property
    def effect(self) -> PolicyEffect:
        return self._effect

    @property
    def actions(self) -> tuple[str, ...]:
        return self._actions

    @property
    def resources(self) -> tuple[str, ...]:
        return self._resources

    @property
    def conditions(self) -> dict[str, Any] | None:
        return self._conditions

    @property
    def status(self) -> PolicyStatus:
        return self._status

    @property
    def is_active(self) -> bool:
        return self._status is PolicyStatus.ACTIVE

    @property
    def created_at(self) -> datetime:
        return self._created_at

    @property
    def updated_at(self) -> datetime:
        return self._updated_at

    @property
    def version(self) -> int:
        return self._version

    # -- transitions -------------------------------------------------------
    def activate(self, *, now: datetime) -> None:
        """Activate the policy."""
        self._status = PolicyStatus.ACTIVE
        self._updated_at = now

    def deactivate(self, *, now: datetime) -> None:
        """Deactivate the policy."""
        self._status = PolicyStatus.INACTIVE
        self._updated_at = now

    def update_rules(
        self,
        *,
        actions: Sequence[str],
        resources: Sequence[str],
        conditions: dict[str, Any] | None,
        now: datetime,
    ) -> None:
        """Update policy rules and conditions."""
        validate_condition_ast(conditions)
        self._actions = tuple(actions)
        self._resources = tuple(resources)
        self._conditions = conditions
        self._updated_at = now

    def matches_action(self, action: str) -> bool:
        """Check if policy covers this action."""
        for pattern in self._actions:
            if pattern == "*" or pattern == action:
                return True
            if pattern.endswith(".*"):
                prefix = pattern[:-2]
                if action == prefix or action.startswith(f"{prefix}."):
                    return True
        return False

    def matches_resource(self, resource_type: str, resource_id: str | None = None) -> bool:
        """Check if policy covers this resource."""
        for pattern in self._resources:
            if pattern == "*":
                return True
            if pattern == resource_type or pattern == f"{resource_type}:*":
                return True
            if resource_id and pattern == f"{resource_type}:{resource_id}":
                return True
        return False


def _validate_policy_name(name: str) -> str:
    cleaned = name.strip()
    if not (2 <= len(cleaned) <= 128):
        raise InvalidValue(
            "policy name must be 2-128 characters",
            details={"name": name},
        )
    return cleaned


__all__ = [
    "AccessPolicy",
    "ConditionOperator",
    "PolicyEffect",
    "PolicyStatus",
    "evaluate_condition",
    "validate_condition_ast",
]
