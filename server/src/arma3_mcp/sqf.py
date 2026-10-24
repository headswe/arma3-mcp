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


# Defines the Eden editor helpers. Eden entities are referred to by their Eden id (a number, from
# get3DENEntityID); markers can also be given by name. Throws when the editor is not open.
EDEN = """
if (!is3DEN || {is3DENPreview}) then { throw "the Eden editor is not open (or a preview is running); the eden_* tools only work while editing a scenario" };
private _edenKind = {
    if (_this isEqualType "") exitWith { "marker" };
    if (_this isEqualType []) exitWith { "waypoint" };
    if (_this isEqualType grpNull) exitWith { "group" };
    if (_this isEqualType 0) exitWith { "comment" };
    if (_this isKindOf "EmptyDetector") exitWith { "trigger" };
    if (_this isKindOf "Logic") exitWith { "logic" };
    "object"
};
private _edenSection = {
    switch (_this) do {
        case "marker": { "Marker" };
        case "waypoint": { "Waypoint" };
        case "group": { "Group" };
        case "comment": { "Comment" };
        case "trigger": { "Trigger" };
        case "logic": { "Logic" };
        default { "Object" };
    }
};
private _edenNeed = {
    private _ref = _this;
    private _e = if (_ref isEqualType "") then { _ref } else { get3DENEntity _ref };
    if (isNil "_e" || {_e isEqualTo objNull} || {_e isEqualTo grpNull} || {_e isEqualTo -1} || {get3DENEntityID _e < 0}) then {
        throw format ["no Eden entity %1 (use an id from eden_list_entities, or a marker name)", _ref];
    };
    _e
};
private _edenAttr = {
    params ["_e", "_name", ["_default", ""]];
    private _v = _e get3DENAttribute _name;
    if (_v isEqualTo [] || {isNil { _v select 0 }}) then { _default } else { _v select 0 }
};
private _edenBrief = {
    private _e = _this;
    private _kind = _e call _edenKind;
    private _b = createHashMapFromArray [["id", get3DENEntityID _e], ["kind", _kind]];
    switch (_kind) do {
        case "object";
        case "trigger";
        case "logic": {
            _b set ["class", typeOf _e];
            _b set ["name", [_e, "name"] call _edenAttr];
            _b set ["position", [_e, "position", []] call _edenAttr];
            _b set ["rotation", [_e, "rotation", []] call _edenAttr];
            if (_e isKindOf "CAManBase") then { _b set ["group", get3DENEntityID group _e] };
            if (_kind == "object" && {!(_e isKindOf "CAManBase")}) then {
                _b set ["crew", (crew _e) apply { get3DENEntityID _x }];
            };
        };
        case "group": {
            _b set ["class", str side _e];
            _b set ["name", [_e, "groupID", groupId _e] call _edenAttr];
            _b set ["units", (units _e) apply { get3DENEntityID _x }];
            _b set ["waypoints", ((waypoints _e) apply { get3DENEntityID _x }) select { _x >= 0 }];
        };
        case "waypoint": {
            _b set ["class", waypointType _e];
            _b set ["group", get3DENEntityID (_e select 0)];
            _b set ["index", _e select 1];
            _b set ["position", [_e, "position", []] call _edenAttr];
        };
        case "marker": {
            _b set ["class", markerType _e];
            _b set ["name", _e];
            _b set ["text", markerText _e];
            _b set ["position", [_e, "position", []] call _edenAttr];
        };
        default {
            _b set ["position", [_e, "position", []] call _edenAttr];
        };
    };
    _b
};
"""
