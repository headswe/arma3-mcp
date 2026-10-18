"""SQF source helpers."""

from __future__ import annotations


def strip_comments(code: str) -> str:
    """Removes // and /* */ comments (which compile does not accept), leaving string literals alone.

    SQF strings use "..." or '...' and escape a quote by doubling it, so toggling on every quote
    character tracks them correctly. Line breaks inside block comments are kept so line numbers
    in error messages still match.
    """
    out = []
    i, n = 0, len(code)
    quote = None
    while i < n:
        c = code[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = None
            i += 1
        elif c in "\"'":
            quote = c
            out.append(c)
            i += 1
        elif code.startswith("//", i):
            end = code.find("\n", i)
            i = n if end < 0 else end
        elif code.startswith("/*", i):
            end = code.find("*/", i + 2)
            block = code[i : n if end < 0 else end + 2]
            out.append("\n" * block.count("\n") or " ")
            i += len(block)
        else:
            out.append(c)
            i += 1
    return "".join(out)


# Defines _resolve: turns an object reference (netId "2:345", a global variable name, or
# "player") into an object, or objNull.
RESOLVE = """
private _resolve = {
    params ["_ref"];
    if (_ref isEqualType objNull) exitWith { _ref };
    if !(_ref isEqualType "") exitWith { objNull };
    if (toLower _ref == "player") exitWith { player };
    private _o = objectFromNetId _ref;
    if (isNull _o) then { _o = missionNamespace getVariable [_ref, objNull] };
    if !(_o isEqualType objNull) then { _o = objNull };
    _o
};
private _need = {
    private _o = _this call _resolve;
    if (isNull _o) then { throw format ["no object %1 (use a netId such as ""2:345"", a variable name or ""player"")", _this] };
    _o
};
private _round = { _this apply { round (_x * 10) / 10 } };
private _sideName = {
    switch (_this) do {
        case west: { "WEST" };
        case east: { "EAST" };
        case independent: { "GUER" };
        case civilian: { "CIV" };
        default { str _this };
    }
};
private _brief = {
    private _o = _this;
    createHashMapFromArray [
        ["net_id", netId _o],
        ["type", typeOf _o],
        ["name", if (_o isKindOf "CAManBase") then { name _o } else { getText (configOf _o >> "displayName") }],
        ["side", (if (_o isKindOf "CAManBase") then { side group _o } else { side _o }) call _sideName],
        ["pos", getPosATL _o call _round],
        ["alive", alive _o]
    ]
};
"""

SIDES = {
    "west": "WEST", "blufor": "WEST", "blue": "WEST",
    "east": "EAST", "opfor": "EAST", "red": "EAST",
    "independent": "GUER", "resistance": "GUER", "guer": "GUER", "indfor": "GUER", "green": "GUER",
    "civilian": "CIV", "civ": "CIV",
}


def side_name(side: str | None) -> str:
    if not side:
        return ""
    key = side.strip().lower()
    if key not in SIDES:
        raise ValueError(f"unknown side {side!r}; use west/blufor, east/opfor, independent or civilian")
    return SIDES[key]
