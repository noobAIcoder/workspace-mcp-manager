from __future__ import annotations

import dataclasses
import re
from collections import defaultdict, deque
from typing import Any, Iterable, Sequence


def _valid_repo_path_for_glob(value: str) -> bool:
    if not value or value == "(none)":
        return False
    if value.startswith(("/", "./", "../")) or re.match(r"^[A-Za-z]:", value):
        return False
    if "//" in value or "\\" in value or "/../" in f"/{value}/" or "/./" in f"/{value}/":
        return False
    return True


_GLOB_MAX_CODEPOINT = 0x10FFFF
_GLOB_SLASH = ord("/")
_GLOB_ANY_NON_SLASH = ((0, _GLOB_SLASH - 1), (_GLOB_SLASH + 1, _GLOB_MAX_CODEPOINT))


def _normalize_intervals(intervals: Iterable[tuple[int, int]]) -> tuple[tuple[int, int], ...]:
    values = sorted((lo, hi) for lo, hi in intervals if lo <= hi)
    merged: list[tuple[int, int]] = []
    for lo, hi in values:
        if merged and lo <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], hi))
        else:
            merged.append((lo, hi))
    return tuple(merged)


def _without_slash(lo: int, hi: int) -> list[tuple[int, int]]:
    if hi < _GLOB_SLASH or lo > _GLOB_SLASH:
        return [(lo, hi)]
    result: list[tuple[int, int]] = []
    if lo < _GLOB_SLASH:
        result.append((lo, _GLOB_SLASH - 1))
    if hi > _GLOB_SLASH:
        result.append((_GLOB_SLASH + 1, hi))
    return result


def _parse_class(body: str) -> tuple[str, tuple[tuple[int, int], ...] | None, str]:
    if not body:
        return "invalid", None, "empty character class"
    if body[0] in {"!", "^"}:
        return "unsupported", None, "negated character classes are not supported"
    intervals: list[tuple[int, int]] = []
    i = 0
    while i < len(body):
        if i + 2 < len(body) and body[i + 1] == "-":
            lo, hi = ord(body[i]), ord(body[i + 2])
            if lo > hi:
                return "invalid", None, f"descending character range {body[i:i + 3]!r}"
            intervals.extend(_without_slash(lo, hi))
            i += 3
            continue
        cp = ord(body[i])
        if cp != _GLOB_SLASH:
            intervals.append((cp, cp))
        i += 1
    normalized = _normalize_intervals(intervals)
    if not normalized:
        return "invalid", None, "character class cannot match a path-segment character"
    return "valid", normalized, ""


def _segment_tokens(segment: str) -> tuple[str, list[tuple[str, Any]] | None, str]:
    tokens: list[tuple[str, Any]] = []
    i = 0
    while i < len(segment):
        ch = segment[i]
        if ch == "*":
            if i + 1 < len(segment) and segment[i + 1] == "*":
                return "invalid", None, "recursive ** must be a complete path segment"
            tokens.append(("star", None))
            i += 1
            continue
        if ch == "?":
            tokens.append(("predicate", _GLOB_ANY_NON_SLASH))
            i += 1
            continue
        if ch == "[":
            close = segment.find("]", i + 1)
            if close < 0:
                return "invalid", None, "unterminated character class"
            status, intervals, reason = _parse_class(segment[i + 1:close])
            if status != "valid":
                return status, None, reason
            tokens.append(("predicate", intervals))
            i = close + 1
            continue
        if ch in "{}":
            return "unsupported", None, "brace expansion is not supported"
        tokens.append(("predicate", ((ord(ch), ord(ch)),)))
        i += 1
    return "valid", tokens, ""


def path_glob_status(value: str) -> tuple[str, str]:
    if not _valid_repo_path_for_glob(value):
        return "invalid", "path is not a normalized repository-relative path"
    segments = value.split("/")
    for segment in segments:
        if segment == "**":
            continue
        status, _, reason = _segment_tokens(segment)
        if status != "valid":
            return status, reason
    return "valid", ""


@dataclasses.dataclass
class _GlobNfa:
    start: int
    accepts: set[int]
    transitions: dict[int, list[tuple[tuple[tuple[int, int], ...], int]]]
    epsilons: dict[int, set[int]]
    states: int


def _glob_builder() -> dict[str, Any]:
    return {"next": 0, "transitions": defaultdict(list), "epsilons": defaultdict(set)}


def _glob_state(builder: dict[str, Any]) -> int:
    state = builder["next"]
    builder["next"] += 1
    return state


def _glob_transition(builder: dict[str, Any], source: int, predicate: tuple[tuple[int, int], ...], target: int) -> None:
    builder["transitions"][source].append((predicate, target))


def _glob_epsilon(builder: dict[str, Any], source: int, target: int) -> None:
    builder["epsilons"][source].add(target)


def _compile_segment(builder: dict[str, Any], current: int, segment: str) -> int:
    status, tokens, reason = _segment_tokens(segment)
    if status != "valid" or tokens is None:
        raise ValueError(reason)
    if segment == "*":
        target = _glob_state(builder)
        _glob_transition(builder, current, _GLOB_ANY_NON_SLASH, target)
        _glob_transition(builder, target, _GLOB_ANY_NON_SLASH, target)
        return target
    for kind, value in tokens:
        if kind == "star":
            target = _glob_state(builder)
            _glob_epsilon(builder, current, target)
            _glob_transition(builder, current, _GLOB_ANY_NON_SLASH, current)
            current = target
        else:
            target = _glob_state(builder)
            _glob_transition(builder, current, value, target)
            current = target
    return current


def _compile_glob(pattern: str) -> _GlobNfa:
    status, reason = path_glob_status(pattern)
    if status != "valid":
        raise ValueError(f"{status}: {reason}")
    builder = _glob_builder()
    start = _glob_state(builder)
    segments = pattern.split("/")
    collapsed: list[str] = []
    for segment in segments:
        if segment == "**" and collapsed and collapsed[-1] == "**":
            continue
        collapsed.append(segment)
    if collapsed == ["**"]:
        current = _glob_state(builder)
        _glob_transition(builder, start, _GLOB_ANY_NON_SLASH, current)
        _glob_transition(builder, current, _GLOB_ANY_NON_SLASH, current)
        after_slash = _glob_state(builder)
        _glob_transition(builder, current, ((_GLOB_SLASH, _GLOB_SLASH),), after_slash)
        _glob_transition(builder, after_slash, _GLOB_ANY_NON_SLASH, current)
        return _GlobNfa(start, {current}, dict(builder["transitions"]), dict(builder["epsilons"]), builder["next"])

    current = start
    concrete_seen = False
    for segment in collapsed:
        if segment == "**":
            if not concrete_seen:
                inside = _glob_state(builder)
                _glob_transition(builder, current, _GLOB_ANY_NON_SLASH, inside)
                _glob_transition(builder, inside, _GLOB_ANY_NON_SLASH, inside)
                _glob_transition(builder, inside, ((_GLOB_SLASH, _GLOB_SLASH),), current)
            else:
                after_slash = _glob_state(builder)
                inside = _glob_state(builder)
                _glob_transition(builder, current, ((_GLOB_SLASH, _GLOB_SLASH),), after_slash)
                _glob_transition(builder, after_slash, _GLOB_ANY_NON_SLASH, inside)
                _glob_transition(builder, inside, _GLOB_ANY_NON_SLASH, inside)
                _glob_epsilon(builder, inside, current)
            continue
        if concrete_seen:
            after_sep = _glob_state(builder)
            _glob_transition(builder, current, ((_GLOB_SLASH, _GLOB_SLASH),), after_sep)
            current = after_sep
        current = _compile_segment(builder, current, segment)
        concrete_seen = True
    return _GlobNfa(start, {current}, dict(builder["transitions"]), dict(builder["epsilons"]), builder["next"])


def _union_nfas(nfas: Sequence[_GlobNfa]) -> _GlobNfa:
    transitions: dict[int, list[tuple[tuple[tuple[int, int], ...], int]]] = defaultdict(list)
    epsilons: dict[int, set[int]] = defaultdict(set)
    start = 0
    offset = 1
    accepts: set[int] = set()
    for nfa in nfas:
        epsilons[start].add(nfa.start + offset)
        for source, entries in nfa.transitions.items():
            transitions[source + offset].extend((predicate, target + offset) for predicate, target in entries)
        for source, targets in nfa.epsilons.items():
            epsilons[source + offset].update(target + offset for target in targets)
        accepts.update(state + offset for state in nfa.accepts)
        offset += nfa.states
    return _GlobNfa(start, accepts, dict(transitions), dict(epsilons), offset)


def _epsilon_closure(nfa: _GlobNfa, states: Iterable[int]) -> frozenset[int]:
    closure = set(states)
    stack = list(closure)
    while stack:
        state = stack.pop()
        for target in nfa.epsilons.get(state, set()):
            if target not in closure:
                closure.add(target)
                stack.append(target)
    return frozenset(closure)


def _predicate_matches(predicate: tuple[tuple[int, int], ...], codepoint: int) -> bool:
    return any(lo <= codepoint <= hi for lo, hi in predicate)


def _nfa_step(nfa: _GlobNfa, states: frozenset[int], codepoint: int) -> frozenset[int]:
    targets: set[int] = set()
    for state in states:
        for predicate, target in nfa.transitions.get(state, []):
            if _predicate_matches(predicate, codepoint):
                targets.add(target)
    return _epsilon_closure(nfa, targets) if targets else frozenset()


def _alphabet_representatives(*nfas: _GlobNfa) -> list[int]:
    boundaries = {0, _GLOB_MAX_CODEPOINT + 1}
    for nfa in nfas:
        for entries in nfa.transitions.values():
            for predicate, _ in entries:
                for lo, hi in predicate:
                    boundaries.add(lo)
                    if hi < _GLOB_MAX_CODEPOINT:
                        boundaries.add(hi + 1)
    ordered = sorted(boundaries)
    return [left for left, right in zip(ordered, ordered[1:]) if left < right]


def glob_subset(touched: str, owners: Sequence[str], state_limit: int = 20000) -> tuple[str, str]:
    touched_status, touched_reason = path_glob_status(touched)
    if touched_status != "valid":
        return touched_status, f"touched pattern {touched!r}: {touched_reason}"
    if not owners:
        return "not_contained", "no selected module ownership patterns"
    owner_nfas: list[_GlobNfa] = []
    for owner in owners:
        owner_status, owner_reason = path_glob_status(owner)
        if owner_status != "valid":
            return owner_status, f"owner pattern {owner!r}: {owner_reason}"
        owner_nfas.append(_compile_glob(owner))
    touched_nfa = _compile_glob(touched)
    owner_nfa = _union_nfas(owner_nfas)
    alphabet = _alphabet_representatives(touched_nfa, owner_nfa)
    initial = (_epsilon_closure(touched_nfa, {touched_nfa.start}), _epsilon_closure(owner_nfa, {owner_nfa.start}))
    queue: deque[tuple[frozenset[int], frozenset[int]]] = deque([initial])
    seen = {initial}
    while queue:
        if len(seen) > state_limit:
            return "unestablished", f"containment analysis exceeded {state_limit} product states"
        touched_states, owner_states = queue.popleft()
        if touched_nfa.accepts.intersection(touched_states) and not owner_nfa.accepts.intersection(owner_states):
            return "not_contained", "touched pattern admits at least one path outside selected module ownership"
        for codepoint in alphabet:
            next_touched = _nfa_step(touched_nfa, touched_states, codepoint)
            if not next_touched:
                continue
            next_owner = _nfa_step(owner_nfa, owner_states, codepoint)
            pair = (next_touched, next_owner)
            if pair not in seen:
                seen.add(pair)
                queue.append(pair)
    return "contained", "touched pattern is contained by selected module ownership"



__all__ = [name for name in globals() if not name.startswith("__")]
