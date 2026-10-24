"""MCP server exposing a running Arma 3 game: run SQF, inspect the mission, read the RPT log."""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import math
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
    from mcp.server.mcpserver.exceptions import ToolError

    _MCP2 = True
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server
    from mcp.server.fastmcp.exceptions import ToolError

    _MCP2 = False

from mcp.types import ImageContent, TextContent

from . import __version__
from .bridge import Bridge, BridgeError
from .map_tile import crop_svg, find_icon_png, find_world_svg, svg_extent
from .rpt import RptReader
from .sqf import EDEN, RESOLVE, side_name, strip_comments

INSTRUCTIONS = """\
Controls a running Arma 3 game through the arma3_mcp extension.

- execute_sqf runs any SQF. The value of the last expression is the result, converted with toJSON:
  arrays, hashmaps (-> JSON objects), strings, numbers and booleans map directly; objects, groups,
  sides and other engine types come back as their `str` text. Return hashmaps for structured data.
- Pass data in with `args` (any JSON) instead of formatting it into the code; it arrives as `_this`.
- Code runs unscheduled by default (atomic, in one frame; no sleep/waitUntil). Use scheduled=true
  for code that has to wait. Comments are stripped; #define and #include are not available.
- Objects are referenced by netId ("2:345"), a global variable name, or "player". The list tools
  return netIds you can pass to get_object, teleport, delete_object and to your own SQF
  (objectFromNetId).
- In multiplayer the extension runs on the server; target="server" (default for a dedicated
  server anyway), a client owner id, or a player name/UID runs code on that machine instead.
- Script errors are reported in the RPT log: execute_sqf adds new error lines as rpt_errors when the
  log is on this machine, and read_rpt reads it.
- An ok result from execute_sqf only means the SQF request completed; it does not prove the intended
  game-state change happened. Verify important mutations with the relevant inspection tools before
  reporting success.
- For AI movement, inspect existing waypoints before changing a group's route. list_groups reports
  waypoint indices, types, and positions. Avoid leaving stale exit or attack waypoints ahead of the
  intended route.
- create_marker direction is a heading in degrees, not a compass label. Check list_markers' dir
  value after creating a directional marker instead of assuming its orientation from its text.
- For helicopter transport insertion, use a TR UNLOAD waypoint for the transport group and a matching
  UNLOAD waypoint for the passenger group at the same LZ, synchronize the two waypoints, and put the
  passengers' post-unload route after UNLOAD. Do not report insertion complete until inspection
  confirms the passengers are no longer in the helicopter and are at the intended landing area.
- map_tile clips a diag_exportTerrainSvg map to world bounds and overlays live tactical objects.
  Extracted config icons are used when available; directional marker icons follow markerDir.
- The bridge also answers in the Eden editor (game_status shows game.eden). There, edit the
  scenario with the eden_* tools: they work on Eden entities, are saved with the scenario and
  can be undone. create_object and the other mission tools make temporary objects the editor
  does not save. eden_selection tells you what the user has selected.
- Check game_status first if requests time out.
"""

bridge = Bridge()
rpt = RptReader()
mcp = _Server("arma3", instructions=INSTRUCTIONS)


def _default_dir(name: str) -> Path:
    # svg/<name> in the checkout when run from one; otherwise in the current folder.
    checkout = Path(__file__).resolve().parents[3] / "svg" / name
    return checkout if checkout.is_dir() else Path("svg") / name


map_svg_dir = _default_dir("tile-svg")
map_icon_dir = _default_dir("icons")


def _target(target: str | int | None) -> str | int:
    if target is None or target == "":
        return "local"
    if isinstance(target, int):
        return target
    return int(target) if target.strip().isdigit() else target.strip()


async def _run(
    code: str,
    args: Any = None,
    *,
    target: str | int = "local",
    timeout: float = 15.0,
    scheduled: bool = False,
) -> Any:
    """Runs a tool's SQF with the RESOLVE helpers in scope; returns the result or raises ToolError."""
    mark = rpt.mark()
    try:
        res = await bridge.execute(
            strip_comments(RESOLVE + code), args, scheduled=scheduled, target=_target(target), timeout=timeout
        )
    except BridgeError as exc:
        raise ToolError(str(exc)) from None
    if not res.get("ok"):
        message = res.get("error") or "the code failed"
        errors = rpt.errors_since(mark)
        if errors:
            message += "\nRPT:\n" + "\n".join(errors)
        raise ToolError(message)
    return res.get("result")


def _side(side: str | None) -> str:
    try:
        return side_name(side)
    except ValueError as exc:
        raise ToolError(str(exc)) from None


# ---------------------------------------------------------------- running code


@mcp.tool()
async def execute_sqf(
    code: str,
    args: Any = None,
    scheduled: bool = False,
    target: str = "local",
    timeout: float = 15.0,
) -> dict:
    """Run SQF code in the game and return its result as JSON.

    The last expression's value is the result (converted with toJSON; objects and other engine
    types become strings). `args` is any JSON value and arrives in the code as `_this`.

    Args:
        code: SQF source, e.g. `count allUnits` or `allPlayers apply { name _x }`.
        args: Optional data passed to the code as `_this`.
        scheduled: Run in the scheduled environment (allows sleep/waitUntil). Default: unscheduled.
        target: Where to run: "local" (the machine with the extension, normally the server),
            "server", a client's owner id, or a player's name or UID.
        timeout: Seconds to wait for the answer.

    Returns {"ok": true, "type": typeName, "result": value, "time_ms": n} or
    {"ok": false, "error": message}, plus "rpt_errors" when the RPT log gained error lines.
    """
    mark = rpt.mark()
    try:
        res = await bridge.execute(
            strip_comments(code), args, scheduled=scheduled, target=_target(target), timeout=timeout
        )
    except BridgeError as exc:
        raise ToolError(str(exc)) from None
    errors = rpt.errors_since(mark)
    if errors:
        res["rpt_errors"] = errors
    return res


@mcp.tool()
async def game_status() -> dict:
    """Check the connection: whether the extension answers, whether a mission is polling for
    requests, and how many requests are queued or running."""
    try:
        status = await bridge.status()
    except BridgeError as exc:
        return {"connected": False, "address": bridge.address, "error": str(exc)}
    age = status.get("last_poll_ms_ago")
    status["connected"] = True
    status["mission_running"] = age is not None and age < 3000
    rpt_file = rpt.latest()
    status["rpt_file"] = str(rpt_file) if rpt_file else None
    return status


# ---------------------------------------------------------------- mission state


@mcp.tool()
async def mission_info(target: str = "local") -> dict:
    """Mission, world, game version, time, date, weather, FPS, entity counts and the local player."""
    return await _run(
        """
private _player = if (hasInterface && {!isNull player}) then { player call _brief } else { createHashMap };
createHashMapFromArray [
    ["product", productVersion],
    ["mission", missionName],
    ["briefing_name", briefingName],
    ["world", worldName],
    ["world_size", worldSize],
    ["multiplayer", isMultiplayer],
    ["dedicated", isDedicated],
    ["is_server", isServer],
    ["has_interface", hasInterface],
    ["editor", is3DEN && {!is3DENPreview}],
    ["editor_preview", is3DENPreview],
    ["time", time],
    ["server_time", serverTime],
    ["daytime", dayTime],
    ["date", date],
    ["time_multiplier", timeMultiplier],
    ["weather", createHashMapFromArray [["overcast", overcast], ["fog", fog], ["rain", rain], ["wind", wind], ["lightnings", lightnings], ["waves", waves]]],
    ["fps", diag_fps],
    ["fps_min", diag_fpsMin],
    ["counts", createHashMapFromArray [
        ["units", count allUnits],
        ["dead", count allDeadMen],
        ["vehicles", count vehicles],
        ["groups", count allGroups],
        ["players", count (allPlayers - entities "HeadlessClient_F")]
    ]],
    ["player", _player]
]
""",
        target=target,
    )


@mcp.tool()
async def list_players() -> list:
    """Connected players: name, UID, owner id, netId, side, group, role, position, vehicle, damage."""
    return await _run(
        """
(allPlayers - entities "HeadlessClient_F") apply {
    private _p = _x call _brief;
    _p set ["uid", getPlayerUID _x];
    _p set ["owner", owner _x];
    _p set ["group", groupId group _x];
    _p set ["role", roleDescription _x];
    _p set ["vehicle", if (isNull objectParent _x) then { "" } else { typeOf objectParent _x }];
    _p set ["damage", damage _x];
    _p set ["admin", if (isServer) then { admin owner _x } else { -1 }];
    _p
}
"""
    )


_NEAR = """
private _center = [];
if (_near isEqualType "" && {_near != ""}) then { _center = getPosATL (_near call _need) };
if (_near isEqualType [] && {count _near >= 2}) then { _center = _near };
if (_center isNotEqualTo []) then {
    _list = _list select { _x distance2D _center <= _radius };
    _list = [_list, [_center], { _x distance2D _input0 }, "ASCEND"] call BIS_fnc_sortBy;
};
"""


@mcp.tool()
async def list_units(
    side: str | None = None,
    near: str | list[float] | None = None,
    radius: float = 1000,
    kind_of: str = "",
    who: str = "all",
    include_dead: bool = False,
    limit: int = 100,
) -> dict:
    """List units (infantry). Filters combine.

    Args:
        side: west/blufor, east/opfor, independent or civilian.
        near: Only units within `radius` metres of this object (netId, variable name, "player")
            or position [x, y]; sorted nearest first.
        radius: Metres, used with `near`.
        kind_of: Only units whose class inherits from this (e.g. "B_Soldier_base_F").
        who: "all", "players" or "ai".
        include_dead: Include dead bodies.
        limit: Maximum units returned; "total" says how many matched.
    """
    return await _run(
        """
params ["_side", "_near", "_radius", "_kind", "_who", "_includeDead", "_limit"];
private _list = if (_includeDead) then { allUnits + allDeadMen } else { allUnits };
if (_side != "") then { _list = _list select { ((side group _x) call _sideName) == _side } };
if (_kind != "") then { _list = _list select { _x isKindOf _kind } };
if (_who == "players") then { _list = _list select { isPlayer _x } };
if (_who == "ai") then { _list = _list select { !isPlayer _x } };
"""
        + _NEAR
        + """
createHashMapFromArray [
    ["total", count _list],
    ["units", (_list select [0, _limit]) apply {
        private _u = _x call _brief;
        _u set ["group", groupId group _x];
        _u set ["leader", leader group _x == _x];
        _u set ["player", isPlayer _x];
        _u set ["vehicle", if (isNull objectParent _x) then { "" } else { netId objectParent _x }];
        _u set ["behaviour", behaviour _x];
        _u set ["damage", damage _x];
        _u set ["rank", rank _x];
        _u
    }]
]
""",
        [_side(side), near or "", radius, kind_of, who, include_dead, limit],
    )


@mcp.tool()
async def list_vehicles(
    side: str | None = None,
    near: str | list[float] | None = None,
    radius: float = 1000,
    kind_of: str = "AllVehicles",
    limit: int = 100,
) -> dict:
    """List vehicles with their crew, fuel and damage.

    Args:
        side: west/blufor, east/opfor, independent or civilian (empty vehicles are civilian).
        near: Object (netId, variable name, "player") or position [x, y]; sorted nearest first.
        radius: Metres, used with `near`.
        kind_of: Base class filter, e.g. "Car", "Tank", "Air", "Ship". "AllVehicles" leaves out
            weapon holders and other props; "All" includes them.
        limit: Maximum vehicles returned; "total" says how many matched.
    """
    return await _run(
        """
params ["_side", "_near", "_radius", "_kind", "_limit"];
private _list = vehicles select { _x isKindOf _kind };
if (_side != "") then { _list = _list select { ((side _x) call _sideName) == _side } };
"""
        + _NEAR
        + """
createHashMapFromArray [
    ["total", count _list],
    ["vehicles", (_list select [0, _limit]) apply {
        private _v = _x call _brief;
        _v set ["crew", (crew _x) apply { name _x }];
        _v set ["damage", damage _x];
        _v set ["fuel", fuel _x];
        _v set ["can_move", canMove _x];
        _v set ["locked", locked _x];
        _v
    }]
]
""",
        [_side(side), near or "", radius, kind_of, limit],
    )


@mcp.tool()
async def list_groups(side: str | None = None, limit: int = 100) -> dict:
    """List groups with unit counts, behaviour, combat mode, position, and waypoint details.

    Waypoint indices are zero-based; index 0 is the group's starting position. current_waypoint
    is the index of the active waypoint in waypoint_details (equal to the waypoint count once all
    are completed).
    """
    return await _run(
        """
params ["_side", "_limit"];
private _list = allGroups;
if (_side != "") then { _list = _list select { ((side _x) call _sideName) == _side } };
createHashMapFromArray [
    ["total", count _list],
    ["groups", (_list select [0, _limit]) apply {
        private _group = _x;
        private _leader = leader _group;
        private _waypointDetails = [];
        {
            private _waypoint = _x;
            _waypointDetails pushBack createHashMapFromArray [
                ["index", _forEachIndex],
                ["type", waypointType _waypoint],
                ["pos", waypointPosition _waypoint call _round],
                ["completion_radius", waypointCompletionRadius _waypoint]
            ];
        } forEach (waypoints _group);
        createHashMapFromArray [
            ["net_id", netId _group],
            ["id", groupId _group],
            ["side", (side _group) call _sideName],
            ["leader", if (isNull _leader) then { "" } else { name _leader }],
            ["leader_net_id", netId _leader],
            ["units", count units _group],
            ["alive", { alive _x } count units _group],
            ["players", { isPlayer _x } count units _group],
            ["behaviour", combatBehaviour _group],
            ["combat_mode", combatMode _group],
            ["formation", formation _group],
            ["waypoints", count waypoints _group],
            ["current_waypoint", currentWaypoint _group],
            ["waypoint_details", _waypointDetails],
            ["pos", if (isNull _leader) then { [] } else { getPosATL _leader call _round }]
        ]
    }]
]
""",
        [_side(side), limit],
    )


@mcp.tool()
async def get_object(ref: str, include_loadout: bool = True, include_variables: bool = False) -> dict:
    """Details of one object: class, position, grid, direction, damage, side, group, crew,
    animation, loadout and more.

    Args:
        ref: netId (e.g. "2:345"), global variable name, or "player".
        include_loadout: Include getUnitLoadout for units.
        include_variables: Include the names of the variables set on the object.
    """
    return await _run(
        """
params ["_ref", "_loadout", "_vars"];
private _o = _ref call _need;
private _i = _o call _brief;
_i set ["display_name", getText (configOf _o >> "displayName")];
_i set ["var_name", vehicleVarName _o];
_i set ["parents", [configOf _o, true] call BIS_fnc_returnParents];
_i set ["pos_asl", getPosASL _o call _round];
_i set ["grid", mapGridPosition _o];
_i set ["dir", round getDir _o];
_i set ["velocity", velocity _o call _round];
_i set ["damage", damage _o];
_i set ["local", local _o];
_i set ["simulation", simulationEnabled _o];
_i set ["hidden", isObjectHidden _o];
if (isServer) then { _i set ["owner", owner _o] };
if (_o isKindOf "CAManBase") then {
    _i set ["player", isPlayer _o];
    _i set ["group", groupId group _o];
    _i set ["group_net_id", netId group _o];
    _i set ["leader", leader group _o == _o];
    _i set ["rank", rank _o];
    _i set ["skill", skill _o];
    _i set ["behaviour", behaviour _o];
    _i set ["life_state", lifeState _o];
    _i set ["vehicle", if (isNull objectParent _o) then { "" } else { netId objectParent _o }];
    _i set ["animation", animationState _o];
    _i set ["current_weapon", currentWeapon _o];
    if (_loadout) then { _i set ["loadout", getUnitLoadout _o] };
} else {
    _i set ["crew", (fullCrew _o) apply {
        createHashMapFromArray [["unit", (_x select 0) call _brief], ["role", _x select 1], ["cargo_index", _x select 2], ["turret", _x select 3]]
    }];
    _i set ["fuel", fuel _o];
    _i set ["can_move", canMove _o];
    _i set ["locked", locked _o];
};
if (_vars) then { _i set ["variables", allVariables _o] };
_i
""",
        [ref, include_loadout, include_variables],
    )


# ---------------------------------------------------------------- variables

_NAMESPACE = """
private _target = switch (toLower _ns) do {
    case "mission": { missionNamespace };
    case "ui": { uiNamespace };
    case "profile": { profileNamespace };
    case "parsing": { parsingNamespace };
    case "mission_profile": { missionProfileNamespace };
    default { throw format ["unknown namespace %1 (mission, ui, profile, parsing, mission_profile)", _ns] };
};
if (_ref != "") then { _target = _ref call _need };
"""


@mcp.tool()
async def get_variable(name: str, namespace: str = "mission", object: str = "") -> dict:
    """Read a variable from a namespace (mission, ui, profile, parsing, mission_profile) or,
    with `object` (netId, variable name or "player"), from that object."""
    exists, value = await _run(
        """
params ["_name", "_ns", "_ref"];
"""
        + _NAMESPACE
        + """
[!isNil { _target getVariable _name }, _target getVariable _name]
""",
        [name, namespace, object],
    )
    return {"name": name, "exists": exists, "value": value}


@mcp.tool()
async def set_variable(
    name: str, value: Any, namespace: str = "mission", object: str = "", public: bool = False
) -> dict:
    """Set a variable to a JSON value (null deletes it) in a namespace or on an object.
    `public` broadcasts it to every machine. For values JSON cannot express (objects, code),
    use execute_sqf."""
    await _run(
        """
params ["_name", "_value", "_ns", "_ref", "_public"];
"""
        + _NAMESPACE
        + """
_target setVariable [_name, _value, _public];
true
""",
        [name, value, namespace, object, public],
    )
    return {"name": name, "set": value is not None, "deleted": value is None}


@mcp.tool()
async def list_variables(namespace: str = "mission", object: str = "", contains: str = "", limit: int = 200) -> dict:
    """Names of the variables in a namespace or on an object. Functions (*_fnc_*) are left out
    unless `contains` asks for them."""
    return await _run(
        """
params ["_ns", "_ref", "_contains", "_limit"];
"""
        + _NAMESPACE
        + """
private _names = allVariables _target;
if (_contains == "") then {
    _names = _names select { (_x find "_fnc_") < 0 };
} else {
    _names = _names select { (_x find toLower _contains) >= 0 };
};
_names sort true;
createHashMapFromArray [["total", count _names], ["names", _names select [0, _limit]]]
""",
        [namespace, object, contains, limit],
    )


# ---------------------------------------------------------------- config


def _config_path(path: str) -> tuple[str, list[str]]:
    sep = ">>" if ">>" in path else "/"
    parts = [p.strip().strip('"').strip() for p in path.split(sep)]
    parts = [p for p in parts if p]
    root = "config"
    if parts and parts[0].lower() in ("configfile", "missionconfigfile", "campaignconfigfile"):
        root = {"configfile": "config", "missionconfigfile": "mission", "campaignconfigfile": "campaign"}[parts.pop(0).lower()]
    return root, parts


@mcp.tool()
async def get_config(path: str, limit: int = 200) -> dict:
    """Read a config class or property: its properties (inherited ones included), parent classes
    and subclasses.

    Args:
        path: e.g. "CfgVehicles/B_MRAP_01_F", "CfgWeapons >> arifle_MX_F", or
            "missionConfigFile >> CfgRespawnInventory" for the mission's description.ext.
        limit: Maximum subclass names returned.
    """
    root, parts = _config_path(path)
    return await _run(
        """
params ["_root", "_parts", "_limit"];
private _cfg = switch (_root) do {
    case "mission": { missionConfigFile };
    case "campaign": { campaignConfigFile };
    default { configFile };
};
{ _cfg = _cfg >> _x } forEach _parts;
if (isNull _cfg) then { throw format ["%1 does not exist", _parts joinString " >> "] };
private _value = { if (isNumber _this) then { getNumber _this } else { if (isText _this) then { getText _this } else { getArray _this } } };
if (!isClass _cfg) exitWith { createHashMapFromArray [["path", str _cfg], ["value", _cfg call _value]] };
private _props = createHashMap;
{ _props set [configName _x, _x call _value] } forEach configProperties [_cfg, "!isClass _x", true];
private _classes = ("true" configClasses _cfg) apply { configName _x };
private _parents = [];
private _p = inheritsFrom _cfg;
while { !isNull _p } do { _parents pushBack configName _p; _p = inheritsFrom _p };
createHashMapFromArray [
    ["path", str _cfg],
    ["mod", configSourceMod _cfg],
    ["parents", _parents],
    ["properties", _props],
    ["class_count", count _classes],
    ["classes", _classes select [0, _limit]]
]
""",
        [root, parts, limit],
        timeout=30,
    )


@mcp.tool()
async def search_classes(
    query: str = "",
    section: str = "CfgVehicles",
    kind_of: str = "",
    public_only: bool = True,
    limit: int = 50,
) -> dict:
    """Find config classes by class name or display name (case-insensitive substring).

    Args:
        query: Text to look for, e.g. "hunter" or "MX".
        section: Top-level config class: CfgVehicles, CfgWeapons, CfgMagazines, CfgAmmo,
            CfgGlasses, CfgFactionClasses, ...
        kind_of: Only classes inheriting from this, e.g. "Car", "Man", "Rifle_Base_F".
        public_only: Only classes with scope = 2 (the ones the editor and arsenal show).
        limit: Maximum classes returned; "total" says how many matched.
    """
    return await _run(
        """
params ["_section", "_query", "_base", "_publicOnly", "_limit"];
private _root = configFile >> _section;
if (!isClass _root) then { throw format ["configFile >> %1 does not exist", _section] };
private _q = toLower _query;
private _out = [];
private _total = 0;
{
    private _name = configName _x;
    private _display = getText (_x >> "displayName");
    if (
        (!_publicOnly || {getNumber (_x >> "scope") >= 2})
        && {_q == "" || {(toLower _name find _q) >= 0} || {(toLower _display find _q) >= 0}}
        && {_base == "" || {_name isKindOf [_base, _root]}}
    ) then {
        _total = _total + 1;
        if (count _out < _limit) then {
            _out pushBack createHashMapFromArray [
                ["class", _name],
                ["display_name", _display],
                ["parent", configName inheritsFrom _x],
                ["faction", getText (_x >> "faction")],
                ["mod", configSourceMod _x]
            ];
        };
    };
} forEach ("true" configClasses _root);
createHashMapFromArray [["total", _total], ["classes", _out]]
""",
        [section, query, kind_of, public_only, limit],
        timeout=60,
    )


# ---------------------------------------------------------------- changing the world


@mcp.tool()
async def create_object(
    class_name: str,
    position: list[float] | None = None,
    near: str = "",
    distance: float = 10,
    direction: float = 0,
    side: str | None = None,
    crew: bool = False,
    special: str = "NONE",
) -> dict:
    """Spawn a vehicle, prop or unit (classes from CfgVehicles; see search_classes).

    Args:
        class_name: e.g. "B_MRAP_01_F" or "B_Soldier_F".
        position: [x, y] or [x, y, z] above terrain. If neither this nor `near` is given, spawns
            near the player.
        near: Spawn `distance` metres in front of this object (netId, variable name, "player").
        direction: Heading in degrees.
        side: For units: west, east, independent or civilian (default: the class's own side).
        crew: For vehicles: add the default AI crew.
        special: "NONE", "CAN_COLLIDE" (exact position) or "FLY" (aircraft in the air).
    """
    if position is None and not near:
        near = "player"
    return await _run(
        """
params ["_class", "_pos", "_near", "_distance", "_dir", "_side", "_crew", "_special"];
private _cfg = configFile >> "CfgVehicles" >> _class;
if (!isClass _cfg) then { throw format ["%1 is not a CfgVehicles class (see search_classes)", _class] };
if (_near != "") then {
    private _ref = _near call _need;
    _pos = _ref getPos [_distance, getDir _ref];
};
_pos = +_pos;
if (count _pos == 2) then { _pos pushBack 0 };
private _o = objNull;
if (_class isKindOf "CAManBase") then {
    private _s = if (_side == "") then {
        [east, west, independent, civilian] select (0 max getNumber (_cfg >> "side") min 3)
    } else {
        [west, east, independent, civilian] select (["WEST", "EAST", "GUER", "CIV"] find _side)
    };
    _o = (createGroup [_s, true]) createUnit [_class, _pos, [], 0, "NONE"];
} else {
    _o = createVehicle [_class, _pos, [], 0, _special];
    if (_crew) then { createVehicleCrew _o };
};
_o setDir _dir;
private _i = _o call _brief;
_i set ["display_name", getText (_cfg >> "displayName")];
_i set ["crew", (crew _o) apply { netId _x }];
_i
""",
        [class_name, position or [], near, distance, direction, _side(side), crew, special],
    )


@mcp.tool()
async def delete_object(ref: str, with_crew: bool = True) -> dict:
    """Delete an object by netId or variable name. Players are never deleted; with `with_crew`,
    a vehicle's AI crew goes with it."""
    return await _run(
        """
params ["_ref", "_withCrew"];
private _o = _ref call _need;
if (isPlayer _o) then { throw "refusing to delete a player" };
private _desc = _o call _brief;
if (_withCrew && {!(_o isKindOf "CAManBase")}) then {
    { if (!isPlayer _x) then { _o deleteVehicleCrew _x } } forEach crew _o;
};
deleteVehicle _o;
_desc
""",
        [ref, with_crew],
    )


@mcp.tool()
async def teleport(
    ref: str, position: list[float] | None = None, near: str = "", distance: float = 3, direction: float | None = None
) -> dict:
    """Move an object or unit to a position [x, y(, z above terrain)] or next to another object
    (`near`: netId, variable name or "player"). Units in a vehicle are moved out first. An object
    local to another machine (a player, say) is moved there, so the returned position can still be
    the old one; check with get_object."""
    if position is None and not near:
        raise ToolError("give a position or near")
    return await _run(
        """
params ["_ref", "_pos", "_near", "_distance", "_dir"];
private _o = _ref call _need;
if (_near != "") then {
    private _t = _near call _need;
    _pos = _t getPos [_distance, getDir _t];
};
_pos = +_pos;
if (count _pos == 2) then { _pos pushBack 0 };
// moveOut and setDir only work where the object is local (a player's own machine, say), so the
// move runs there; for a remote object it lands a moment after this returns.
private _move = {
    params ["_o", "_pos", "_dir"];
    if (!isNull objectParent _o) then { moveOut _o };
    _o setPosATL _pos;
    if (_dir >= 0) then { _o setDir _dir };
};
private _local = local _o;
if (_local) then { [_o, _pos, _dir] call _move } else { [[_o, _pos, _dir], _move] remoteExecCall ["call", _o] };
private _i = _o call _brief;
_i set ["moved_on", ["owner machine", "here"] select _local];
_i
""",
        [ref, position or [], near, distance, -1 if direction is None else direction % 360],
    )


@mcp.tool()
async def send_message(text: str, kind: str = "systemChat", everyone: bool = True) -> str:
    """Show a message in game.

    Args:
        text: The message.
        kind: "systemChat", "hint", "titleText" (centre of screen), or "log" (RPT only).
        everyone: Show it on every machine; otherwise only where the extension runs.
    """
    kinds = {"systemchat": "systemChat", "hint": "hint", "titletext": "titleText", "log": "log"}
    if kind.lower() not in kinds:
        raise ToolError(f"kind must be one of {', '.join(kinds.values())}")
    await _run(
        """
params ["_text", "_kind", "_everyone"];
private _to = [clientOwner, 0] select _everyone;
switch (_kind) do {
    case "systemChat": { _text remoteExec ["systemChat", _to] };
    case "hint": { _text remoteExec ["hint", _to] };
    case "titleText": { [_text, "PLAIN DOWN"] remoteExec ["titleText", _to] };
    case "log": { diag_log text _text };
};
true
""",
        [text, kinds[kind.lower()], everyone],
    )
    return "sent"


@mcp.tool()
async def set_environment(
    daytime: float | None = None,
    date: list[int] | None = None,
    overcast: float | None = None,
    fog: float | None = None,
    rain: float | None = None,
    time_multiplier: float | None = None,
    transition: float = 0,
) -> dict:
    """Change time and weather (runs on the server so it applies to everyone).

    Args:
        daytime: Hour of day, e.g. 5.5 for 05:30.
        date: [year, month, day, hour, minute].
        overcast, fog, rain: 0..1. Rain only shows with enough overcast (about 0.7+).
        time_multiplier: 0.1..120.
        transition: Seconds over which weather changes; 0 applies overcast at once.
    """

    def num(v: float | None) -> float:
        return -1 if v is None else v

    return await _run(
        """
params ["_daytime", "_date", "_overcast", "_fog", "_rain", "_mult", "_t"];
if (_date isNotEqualTo []) then { setDate _date };
if (_daytime >= 0) then {
    private _d = date;
    private _minutes = round (_daytime * 60);  // 5.999 -> 06:00, not 05:60
    _d set [3, floor (_minutes / 60) % 24];
    _d set [4, _minutes % 60];
    setDate _d;
};
if (_overcast >= 0) then { _t setOvercast _overcast; if (_t == 0) then { forceWeatherChange } };
if (_fog >= 0) then { _t setFog _fog };
if (_rain >= 0) then { _t setRain _rain };
if (_mult >= 0) then { setTimeMultiplier _mult };
createHashMapFromArray [
    ["date", date], ["daytime", dayTime], ["overcast", overcast], ["overcast_forecast", overcastForecast],
    ["fog", fog], ["rain", rain], ["time_multiplier", timeMultiplier]
]
""",
        [num(daytime), date or [], num(overcast), num(fog), num(rain), num(time_multiplier), transition],
        target="server",
    )


# ---------------------------------------------------------------- markers


@mcp.tool()
async def list_markers(contains: str = "", limit: int = 200) -> dict:
    """Map markers: name, text, type, shape, colour, position, size, direction, channel."""
    return await _run(
        """
params ["_contains", "_limit"];
private _q = toLower _contains;
private _list = allMapMarkers;
if (_q != "") then { _list = _list select { ((toLower _x) find _q) >= 0 || {((toLower markerText _x) find _q) >= 0} } };
createHashMapFromArray [
    ["total", count _list],
    ["markers", (_list select [0, _limit]) apply {
        createHashMapFromArray [
            ["name", _x], ["text", markerText _x], ["type", markerType _x], ["shape", markerShape _x],
            ["color", markerColor _x], ["pos", markerPos _x call _round], ["size", markerSize _x],
            ["dir", markerDir _x], ["alpha", markerAlpha _x], ["channel", markerChannel _x]
        ]
    }]
]
""",
        [contains, limit],
    )


_svg_cache: dict[Path, tuple[float, ET.Element]] = {}


def _load_svg(path: Path) -> ET.Element:
    """Parses a terrain SVG once (they run to tens of MB) and again only when the file changes."""
    mtime = path.stat().st_mtime
    cached = _svg_cache.get(path)
    if cached is None or cached[0] != mtime:
        _svg_cache.clear()  # one world at a time
        cached = _svg_cache[path] = (mtime, ET.fromstring(path.read_text(encoding="utf-8")))
    return cached[1]


@mcp.tool()
async def map_tile(
    top_left: list[float],
    bottom_right: list[float],
    max_size: int = 1024,
    include_dead: bool = False,
    labels: bool = False,
    show_waypoints: bool = False,
) -> list[TextContent | ImageContent]:
    """Return a cropped terrain image with live units, vehicles, markers, and optional AI routes.

    Coordinates use Arma world metres: top_left is [min_x, max_y] and bottom_right is
    [max_x, min_y]. The matching diag_exportTerrainSvg file must be in the configured SVG folder.
    Set show_waypoints=true to draw group routes and waypoint locations.
    """
    if not isinstance(top_left, (list, tuple)) or not isinstance(bottom_right, (list, tuple)):
        raise ToolError("top_left and bottom_right must each be [x, y]")
    if len(top_left) != 2 or len(bottom_right) != 2:
        raise ToolError("top_left and bottom_right must each be [x, y]")
    if any(isinstance(value, bool) for value in (*top_left, *bottom_right)):
        raise ToolError("map bounds must contain finite numbers")
    try:
        left, top = (float(value) for value in top_left)
        right, bottom = (float(value) for value in bottom_right)
    except (TypeError, ValueError):
        raise ToolError("map bounds must contain finite numbers") from None
    if not all(math.isfinite(value) for value in (left, top, right, bottom)):
        raise ToolError("map bounds must contain finite numbers")
    if left >= right or bottom >= top:
        raise ToolError("top_left must be northwest of bottom_right: x must increase and y must decrease")
    if isinstance(max_size, bool) or not isinstance(max_size, int) or not 128 <= max_size <= 2048:
        raise ToolError("max_size must be an integer from 128 to 2048 pixels")

    snapshot = await _run(
        """
params ["_left", "_bottom", "_right", "_top", "_includeDead", "_showWaypoints"];
private _inside = {
    params ["_pos"];
    count _pos >= 2 && {(_pos select 0) >= _left} && {(_pos select 0) <= _right}
        && {(_pos select 1) >= _bottom} && {(_pos select 1) <= _top}
};
private _units = (allUnits + (allDeadMen - allUnits)) select {
    private _pos = getPosATL _x;
    ([_pos] call _inside) && {isNull objectParent _x} && {_includeDead || {alive _x}}
};
private _vehicles = vehicles select { _x isKindOf "AllVehicles" && {[getPosATL _x] call _inside} };
private _markers = allMapMarkers select {
    private _pos = markerPos _x;
    private _size = markerSize _x;
    private _extent = sqrt ((abs (_size select 0)) ^ 2 + (abs (_size select 1)) ^ 2);
    markerAlpha _x > 0
        && {(_pos select 0) + _extent >= _left}
        && {(_pos select 0) - _extent <= _right}
        && {(_pos select 1) + _extent >= _bottom}
        && {(_pos select 1) - _extent <= _top}
};
private _routes = if (_showWaypoints) then {
    allGroups select {count units _x > 0 && {count waypoints _x > 0}}
} else {
    []
};
[
    worldName,
    worldSize,
    _units apply {
        createHashMapFromArray [
            ["kind", "unit"], ["name", name _x], ["side", (side group _x) call _sideName],
            ["type", typeOf _x], ["icon", getText (configOf _x >> "icon")],
            ["pos", getPosATL _x], ["alive", alive _x]
        ]
    },
    _vehicles apply {
        createHashMapFromArray [
            ["kind", "vehicle"], ["name", getText (configOf _x >> "displayName")],
            ["side", (side _x) call _sideName], ["type", typeOf _x],
            ["icon", getText (configOf _x >> "icon")], ["pos", getPosATL _x]
        ]
    },
    _markers apply {
        createHashMapFromArray [
            ["kind", "marker"], ["name", _x], ["text", markerText _x],
            ["color", markerColor _x], ["shape", markerShape _x],
            ["icon", getText (configFile >> "CfgMarkers" >> markerType _x >> "icon")],
            ["pos", markerPos _x],
            ["size", markerSize _x], ["dir", markerDir _x]
        ]
    },
    _routes apply {
        private _group = _x;
        private _points = [getPosATL (leader _group)];
        private _types = [];
        private _currentWaypoint = currentWaypoint _group;
        {
            if ((_x select 1) >= _currentWaypoint) then {
                _points pushBack (waypointPosition _x);
                _types pushBack (waypointType _x);
            };
        } forEach (waypoints _group);
        createHashMapFromArray [
            ["kind", "waypoint_route"], ["name", groupId _group],
            ["side", (side _group) call _sideName], ["points", _points],
            ["waypoint_types", _types]
        ]
    }
]
""",
        [left, bottom, right, top, include_dead, show_waypoints],
        timeout=45.0,
    )
    world, world_size, units, vehicles, markers, routes = snapshot
    try:
        svg_path = find_world_svg(map_svg_dir, str(world))
    except FileNotFoundError as exc:
        raise ToolError(
            f"{exc}. Set ARMA3_MAP_SVG_DIR or use --map-svg-dir to select the exported terrain folder."
        ) from None
    try:
        svg_root = _load_svg(svg_path)
        svg_width, svg_height = svg_extent(svg_root)
    except (OSError, ET.ParseError, ValueError) as exc:
        raise ToolError(f"could not read terrain SVG {svg_path}: {exc}") from None
    if left < 0 or bottom < 0 or right > svg_width or top > svg_height:
        raise ToolError(f"map bounds must fit inside the {svg_width:g} x {svg_height:g} metre terrain SVG")
    if (
        not math.isclose(float(world_size), svg_width, rel_tol=0.01, abs_tol=1)
        or not math.isclose(float(world_size), svg_height, rel_tol=0.01, abs_tol=1)
    ):
        raise ToolError(
            f"terrain SVG {svg_path.name} is {svg_width:g} x {svg_height:g} m but "
            f"the active world reports {world_size:g} m"
        )

    crop_width, crop_height = right - left, top - bottom
    image_width = max(1, round(max_size * crop_width / max(crop_width, crop_height)))
    image_height = max(1, round(max_size * crop_height / max(crop_width, crop_height)))
    overlays = [*units, *vehicles, *markers]
    if show_waypoints:
        overlays.extend(routes)
    icon_cache: dict[str, str | None] = {}
    for item in [*units, *vehicles, *markers]:
        icon = str(item.get("icon", ""))
        if icon not in icon_cache:
            try:
                icon_path = find_icon_png(map_icon_dir, icon)
            except OSError as exc:
                raise ToolError(f"could not search for map icon {icon!r} in {map_icon_dir}: {exc}") from None
            if icon_path is None:
                icon_cache[icon] = None
            else:
                try:
                    icon_cache[icon] = "data:image/png;base64," + base64.b64encode(icon_path.read_bytes()).decode("ascii")
                except OSError as exc:
                    raise ToolError(f"could not read map icon {icon_path}: {exc}") from None
        icon_data = icon_cache[icon]
        if icon_data is None:
            continue
        item["icon_data"] = icon_data
    try:
        image = crop_svg(
            svg_root,
            view_box=(left, svg_height - top, right, svg_height - bottom),
            output_size=(image_width, image_height),
            overlays=overlays,
            world_height=svg_height,
            show_labels=labels,
        )
    except (ET.ParseError, ValueError) as exc:
        raise ToolError(f"could not crop terrain SVG {svg_path}: {exc}") from None
    try:
        import resvg_py
    except ImportError:
        raise ToolError("map_tile needs the resvg-py package (pip install resvg-py)") from None
    png = resvg_py.svg_to_bytes(svg_string=image, width=image_width, height=image_height)

    summary = (
        f"{world} map tile, world bounds [{left:g}, {bottom:g}] to [{right:g}, {top:g}] m; "
        f"{len(units)} units, {len(vehicles)} vehicles and {len(markers)} markers. "
        "Map features include roads, terrain, water and contours. Overlay legend: blue=WEST, red=EAST, "
        "green=GUER, purple=CIV, gray=dead; units are circles, vehicles diamonds, markers use their configured color."
    )
    if show_waypoints:
        summary += " Group routes and remaining waypoints are drawn as dashed side-colored lines and numbered points."
    return [
        TextContent(type="text", text=summary),
        ImageContent(
            type="image",
            data=base64.b64encode(png).decode("ascii"),
            mimeType="image/png",
        ),
    ]


@mcp.tool()
async def create_marker(
    position: list[float] | None = None,
    near: str = "",
    text: str = "",
    name: str = "",
    type: str = "hd_dot",
    color: str = "ColorRed",
    shape: str = "ICON",
    size: list[float] | None = None,
    direction: float = 0,
) -> str:
    """Create a global map marker at a position [x, y] or on an object (`near`). shape is ICON,
    RECTANGLE or ELLIPSE; type is a CfgMarkers class (hd_dot, hd_objective, mil_dot, ...);
    color a CfgMarkerColors class (ColorRed, ColorBLUFOR, ...). Returns the marker name."""
    if position is None and not near:
        raise ToolError("give a position or near")
    if shape.upper() not in ("ICON", "RECTANGLE", "ELLIPSE"):
        raise ToolError("shape must be ICON, RECTANGLE or ELLIPSE")
    return await _run(
        """
params ["_pos", "_near", "_text", "_name", "_type", "_color", "_shape", "_size", "_dir"];
if (_near != "") then { _pos = getPosATL (_near call _need) };
if (_name == "") then { _name = format ["a3mcp_%1_%2", round diag_tickTime, floor random 1e6] };
private _m = createMarker [_name, _pos];
if (_m == "") then { throw format ["a marker named %1 already exists", _name] };
_m setMarkerShapeLocal _shape;
if (_shape == "ICON") then { _m setMarkerTypeLocal _type };
_m setMarkerColorLocal _color;
_m setMarkerTextLocal _text;
_m setMarkerSizeLocal _size;
_m setMarkerDir _dir;  // the last global command broadcasts the whole marker
_m
""",
        [position or [], near, text, name, type, color, shape.upper(), size or [1, 1], direction],
    )


@mcp.tool()
async def delete_marker(name: str) -> str:
    """Delete a map marker by name."""
    await _run(
        """
params ["_name"];
if (markerShape _name == "") then { throw format ["no marker named %1", _name] };
deleteMarker _name;
true
""",
        [name],
    )
    return f"deleted {name}"


# ---------------------------------------------------------------- Eden editor

EdenRef = int | str
_EDEN_KINDS = ("object", "group", "trigger", "logic", "waypoint", "marker", "comment")
_EDEN_MODES = {"object": "Object", "trigger": "Trigger", "logic": "Logic", "waypoint": "Waypoint", "marker": "Marker", "comment": "Comment"}


def _eden_ref(ref: EdenRef) -> EdenRef:
    if isinstance(ref, str) and ref.strip().lstrip("-").isdigit():
        return int(ref)
    return ref


def _eden_refs(refs: list[EdenRef] | EdenRef) -> list[EdenRef]:
    if not isinstance(refs, list):
        refs = [refs]
    if not refs:
        raise ToolError("give at least one Eden entity id")
    return [_eden_ref(r) for r in refs]


async def _eden(code: str, args: Any = None) -> Any:
    """Runs a tool's SQF in the editor with the Eden helpers in scope."""
    return await _run(EDEN + code, args)


@mcp.tool()
async def eden_info() -> dict:
    """The Eden editor's state: scenario, world, camera, what the screen centre points at, and how
    many objects, groups, triggers, logics, waypoints, markers and comments the scenario has, and
    how many are selected. Fails when the editor is not open (in a running mission, say)."""
    return await _eden(
        """
private _all = all3DENEntities;
private _entities = [];
{ _entities append _x } forEach ((_all select [0, 6]) + [_all param [7, []]]);
private _counts = createHashMap;
{ private _k = _x call _edenKind; _counts set [_k, (_counts getOrDefault [_k, 0]) + 1] } forEach _entities;
private _selected = 0;
{ _selected = _selected + count _x } forEach get3DENSelected "";
private _cam = get3DENCamera;
createHashMapFromArray [
    ["scenario", missionName],
    ["world", worldName],
    ["world_size", worldSize],
    ["camera", createHashMapFromArray [
        ["position", getPosASL _cam call _round],
        ["direction", round getDir _cam],
        ["looking_at", screenToWorld [0.5, 0.5] call _round]
    ]],
    ["counts", _counts],
    ["selected", _selected]
]
"""
    )


@mcp.tool()
async def eden_list_entities(kind: str = "", contains: str = "", limit: int = 200) -> dict:
    """List the entities of the scenario open in the Eden editor, with their Eden ids.

    Each has id, kind, class (type; side for a group; waypoint type; marker type), name (variable
    name; callsign for a group; marker name), position and rotation, plus a unit's group, a
    vehicle's crew, and a group's units and waypoints, all as Eden ids.

    Args:
        kind: Only this kind: object, group, trigger, logic, waypoint, marker or comment.
        contains: Only entities whose class or name contains this text (case-insensitive).
        limit: At most this many.
    """
    kind = kind.strip().lower()
    if kind and kind not in _EDEN_KINDS:
        raise ToolError(f"kind must be one of {', '.join(_EDEN_KINDS)}")
    return await _eden(
        """
params ["_kind", "_contains", "_limit"];
private _all = all3DENEntities;
private _list = [];
{ _list append (_x apply { _x call _edenBrief }) } forEach ((_all select [0, 6]) + [_all param [7, []]]);
if (_kind != "") then { _list = _list select { (_x get "kind") == _kind } };
if (_contains != "") then {
    private _c = toLower _contains;
    _list = _list select { (toLower str (_x getOrDefault ["class", ""])) find _c >= 0 || {(toLower str (_x getOrDefault ["name", ""])) find _c >= 0} };
};
createHashMapFromArray [["count", count _list], ["entities", _list select [0, _limit]]]
""",
        [kind, contains, limit],
    )


@mcp.tool()
async def eden_get_entity(id: EdenRef, attributes: list[str] | None = None) -> dict:
    """Everything about one Eden entity: its summary, its connections (sync, group, trigger owner,
    ...), and its attribute values as the editor's attribute window has them.

    Args:
        id: The entity's Eden id (from eden_list_entities), or a marker's name.
        attributes: Only these attribute properties (e.g. ["name", "init", "position"]). By
            default every attribute the entity has: the Cfg3DEN ones for its kind plus the ones
            its class adds.
    """
    return await _eden(
        """
params ["_ref", "_names"];
private _e = _ref call _edenNeed;
private _kind = _e call _edenKind;
private _info = _e call _edenBrief;
if (_names isEqualTo []) then {
    private _cfgs = configProperties [configFile >> "Cfg3DEN" >> (_kind call _edenSection) >> "AttributeCategories", "isClass _x"];
    {
        { _names pushBackUnique getText (_x >> "property") } forEach configProperties [_x >> "Attributes", "isClass _x"];
    } forEach _cfgs;
    if (_e isEqualType objNull) then {
        { _names pushBackUnique getText (_x >> "property") } forEach configProperties [configOf _e >> "Attributes", "isClass _x"];
    };
    _names = _names - [""];
};
private _values = createHashMap;
{
    private _v = _e get3DENAttribute _x;
    if (_v isNotEqualTo [] && {!isNil { _v select 0 }}) then { _values set [_x, _v select 0] };
} forEach _names;
_info set ["attributes", _values];
_info set ["connections", (get3DENConnections _e) apply { [_x select 0, get3DENEntityID (_x select 1)] }];
_info
""",
        [_eden_ref(id), attributes or []],
    )


@mcp.tool()
async def eden_create_entity(
    kind: str,
    class_name: str,
    position: list[float] | None = None,
    attributes: dict[str, Any] | None = None,
    group: EdenRef | None = None,
) -> dict:
    """Place a new entity in the Eden editor, the way dragging it in from the asset browser does
    (it is saved with the scenario and can be undone with eden_action("Undo")).

    Args:
        kind: object (vehicles, props and units; a unit gets a new group unless `group` is
            given), trigger, logic (modules), waypoint, marker or comment.
        class_name: CfgVehicles class for object/logic (e.g. "B_Soldier_F", "ModuleCurator_F"),
            "EmptyDetector" (or another trigger class) for a trigger, a waypoint type for a
            waypoint (e.g. "Move", "SeekAndDestroy", "TR UNLOAD"), a CfgMarkers class for a
            marker (e.g. "mil_dot"), "" for a comment.
        position: [x, y] or [x, y, z] (z above the terrain). Default: where the screen centre
            points.
        attributes: Attribute values to set after placing it, e.g. {"name": "hq", "init":
            "this allowDamage false;", "rotation": [0, 0, 90]} (see eden_get_entity for names).
        group: Eden id of a group to add a unit or waypoint to. Required for waypoints.
    """
    kind = kind.strip().lower()
    if kind not in _EDEN_MODES:
        raise ToolError(f"kind must be one of {', '.join(_EDEN_MODES)}")
    if kind == "waypoint" and group is None:
        raise ToolError("a waypoint needs the Eden id of its group")
    return await _eden(
        """
params ["_mode", "_class", "_pos", "_attrs", "_groupRef"];
if (_pos isEqualTo []) then { _pos = screenToWorld [0.5, 0.5] };
_pos = +_pos;
if (count _pos == 2) then { _pos pushBack 0 };
if (_mode in ["Object", "Logic"] && {!isClass (configFile >> "CfgVehicles" >> _class)}) then {
    throw format ["%1 is not a CfgVehicles class (see search_classes)", _class];
};
if (_mode == "Marker" && {!isClass (configFile >> "CfgMarkers" >> _class)}) then {
    throw format ["%1 is not a CfgMarkers class", _class];
};
private _e = if (_groupRef isEqualTo -1) then {
    create3DENEntity [_mode, _class, _pos]
} else {
    private _g = _groupRef call _edenNeed;
    if !(_g isEqualType grpNull) then { throw format ["%1 is not a group", _groupRef] };
    _g create3DENEntity [_mode, _class, _pos]
};
if (isNil "_e" || {_e isEqualTo objNull} || {_e isEqualTo grpNull} || {_e isEqualTo ""} || {_e isEqualTo []}) then {
    throw format ["the editor did not create %1 %2", _mode, _class];
};
private _failed = [];
{ if !(_e set3DENAttribute [_x, _y]) then { _failed pushBack _x } } forEach _attrs;
private _info = _e call _edenBrief;
if (_failed isNotEqualTo []) then { _info set ["attributes_not_set", _failed] };
_info
""",
        [_EDEN_MODES[kind], class_name, position or [], attributes or {}, -1 if group is None else _eden_ref(group)],
    )


@mcp.tool()
async def eden_set_attributes(ids: list[EdenRef], attributes: dict[str, Any]) -> dict:
    """Set attributes on one or more Eden entities in a single undo step, like changing them in
    the attribute window. Returns each entity's new values, read back from the editor.

    Args:
        ids: Eden ids (or marker names) of the entities to change.
        attributes: Attribute property -> value, e.g. {"position": [1200, 3400, 0],
            "rotation": [0, 0, 180], "name": "convoy_lead", "init": "...", "allowDamage": false,
            "skill": 0.6, "ControlSP": true, "markerText": "LZ"} (see eden_get_entity).
    """
    if not attributes:
        raise ToolError("give at least one attribute")
    return await _eden(
        """
params ["_refs", "_attrs"];
private _ents = _refs apply { _x call _edenNeed };
private _ok = set3DENAttributes ((keys _attrs) apply { [_ents, _x, _attrs get _x] });
createHashMapFromArray [
    ["ok", if (isNil "_ok") then { true } else { _ok }],
    ["entities", _ents apply {
        private _e = _x;
        private _values = createHashMap;
        { private _v = _e get3DENAttribute _x; if (_v isNotEqualTo [] && {!isNil { _v select 0 }}) then { _values set [_x, _v select 0] } } forEach keys _attrs;
        createHashMapFromArray [["id", get3DENEntityID _e], ["attributes", _values]]
    }]
]
""",
        [_eden_refs(ids), attributes],
    )


@mcp.tool()
async def eden_delete_entities(ids: list[EdenRef]) -> dict:
    """Delete Eden entities (one undo step). Deleting a group deletes its units and waypoints."""
    return await _eden(
        """
params ["_refs"];
private _ents = _refs apply { _x call _edenNeed };
private _deleted = _ents apply { _x call _edenBrief };
delete3DENEntities _ents;
createHashMapFromArray [["deleted", _deleted]]
""",
        [_eden_refs(ids)],
    )


@mcp.tool()
async def eden_selection(select: list[EdenRef] | None = None) -> dict:
    """The entities selected in the Eden editor, which is what the user means by "this" or "these".
    With `select`, selects those entities instead (an empty list clears the selection)."""
    return await _eden(
        """
params ["_set", "_refs"];
if (_set) then { set3DENSelected (_refs apply { _x call _edenNeed }) };
private _sel = [];
{ _sel append _x } forEach get3DENSelected "";
createHashMapFromArray [["count", count _sel], ["entities", _sel apply { _x call _edenBrief }]]
""",
        [select is not None, [_eden_ref(r) for r in select or []]],
    )


@mcp.tool()
async def eden_connect(kind: str, from_ids: list[EdenRef], to_id: EdenRef, remove: bool = False) -> dict:
    """Connect Eden entities, or with `remove` disconnect them.

    Args:
        kind: "Sync" (synchronize, e.g. a module to units or two waypoints), "Group" (a unit joins
            the target's group), "TriggerOwner", "WaypointActivation" or "RandomStart".
        from_ids: The entities the connection starts at.
        to_id: The entity it goes to.
    Returns the target's connections afterwards.
    """
    return await _eden(
        """
params ["_kind", "_fromRefs", "_toRef", "_remove"];
private _from = _fromRefs apply { _x call _edenNeed };
private _to = _toRef call _edenNeed;
private _ok = if (_remove) then { remove3DENConnection [_kind, _from, _to] } else { add3DENConnection [_kind, _from, _to] };
createHashMapFromArray [
    ["ok", if (isNil "_ok") then { true } else { _ok }],
    ["connections", (get3DENConnections _to) apply { [_x select 0, get3DENEntityID (_x select 1)] }]
]
""",
        [kind, _eden_refs(from_ids), _eden_ref(to_id), remove],
    )


@mcp.tool()
async def eden_mission_attribute(section: str, name: str = "", value: Any = None) -> dict:
    """Read or set the scenario's own attributes (Attributes menu: General, Multiplayer,
    Environment, ...).

    Args:
        section: "Scenario", "Multiplayer", "Intel" (date, time, weather), "GarbageCollection",
            or another class in Cfg3DEN >> Mission.
        name: The attribute property, e.g. "IntelBriefingName", "MaxPlayers", "IntelTimeOfChanges".
            Empty lists every attribute in the section with its value.
        value: Sets the attribute to this value when given.
    """
    return await _eden(
        """
params ["_section", "_name", "_set", "_value"];
if (_set) then {
    if (_name == "") then { throw "give the attribute name to set" };
    _section set3DENMissionAttribute [_name, _value];
};
private _names = [_name];
if (_name == "") then {
    _names = [];
    {
        { _names pushBackUnique getText (_x >> "property") } forEach configProperties [_x >> "Attributes", "isClass _x"];
    } forEach configProperties [configFile >> "Cfg3DEN" >> "Mission" >> _section >> "AttributeCategories", "isClass _x"];
    _names = _names - [""];
    if (_names isEqualTo []) then { throw format ["no attributes in Cfg3DEN >> Mission >> %1", _section] };
};
private _values = createHashMap;
{
    private _v = _section get3DENMissionAttribute _x;
    if (!isNil "_v") then { _values set [_x, _v] };
} forEach _names;
_values
""",
        [section, name, value is not None, "" if value is None else value],
    )


@mcp.tool()
async def eden_action(action: str) -> str:
    """Run an editor action (do3DENAction), as from the editor's menus and shortcuts, e.g.
    "MissionSave", "MissionPreview", "MissionPreviewMP", "Undo", "Redo". The full list is on the
    BIKI do3DENAction page. While a preview runs, the eden_* tools are unavailable (the other
    tools work in the preview as in any mission)."""
    await _eden(
        """
params ["_action"];
do3DENAction _action;
true
""",
        [action],
    )
    return f"ran {action}"


@mcp.tool()
async def eden_camera(position: list[float] | None = None, entity: EdenRef | None = None) -> dict:
    """Move the Eden editor camera to a position [x, y(, z)] or to an entity, so the user sees it.
    With neither, returns where the camera is."""
    return await _eden(
        """
params ["_pos", "_ref"];
if (_ref isNotEqualTo -1) then {
    private _e = _ref call _edenNeed;
    _pos = [_e, "position", []] call _edenAttr;
    if (_pos isEqualTo [] && {_e isEqualType grpNull}) then { _pos = getPosATL leader _e };
    if (_pos isEqualTo []) then { throw format ["%1 has no position", _ref] };
};
if (_pos isNotEqualTo []) then {
    _pos = +_pos;
    if (count _pos == 2) then { _pos pushBack 0 };
    move3DENCamera [_pos, true];
};
private _cam = get3DENCamera;
createHashMapFromArray [
    ["position", getPosASL _cam call _round],
    ["direction", round getDir _cam],
    ["looking_at", screenToWorld [0.5, 0.5] call _round]
]
""",
        [position or [], -1 if entity is None else _eden_ref(entity)],
    )


# ---------------------------------------------------------------- diagnostics


@mcp.tool()
async def performance(target: str = "local", scripts: int = 50) -> dict:
    """FPS, entity counts and the running scripts (name, file, line) on a machine."""
    return await _run(
        """
params ["_limit"];
createHashMapFromArray [
    ["fps", diag_fps], ["fps_min", diag_fpsMin], ["frame", diag_frameNo], ["time", time],
    ["units", count allUnits], ["dead", count allDeadMen], ["vehicles", count vehicles], ["groups", count allGroups],
    ["sqf_scripts", count diag_activeSQFScripts], ["sqs_scripts", count diag_activeSQSScripts],
    ["fsms", count diag_activeMissionFSMs],
    ["scripts", (diag_activeSQFScripts select [0, _limit]) apply {
        createHashMapFromArray [["name", _x select 0], ["file", _x select 1], ["running", _x select 2], ["line", _x select 3]]
    }]
]
""",
        [scripts],
        target=target,
    )


@mcp.tool()
async def list_mods(include_official: bool = False) -> list:
    """Loaded mods: name, folder, and whether each is official (DLC / base game)."""
    return await _run(
        """
params ["_official"];
(getLoadedModsInfo select { _official || {!(_x select 3)} }) apply {
    createHashMapFromArray [["name", _x select 0], ["dir", _x select 1], ["default", _x select 2], ["official", _x select 3]]
}
""",
        [include_official],
    )


@mcp.tool()
def read_rpt(lines: int = 100, contains: str = "", errors_only: bool = False, file: str = "") -> dict:
    """Read the end of an RPT log on this machine (the newest one unless `file` names another).

    Args:
        lines: How many matching lines from the end.
        contains: Only lines matching this regular expression (case-insensitive).
        errors_only: Only script error lines ("Error ...", "File ..., line N").
        file: An RPT file name from list_rpt_files.
    """
    try:
        path = rpt.resolve(file or None)
        return {"file": str(path), "lines": RptReader.tail(path, lines, contains or None, errors_only)}
    except FileNotFoundError as exc:
        raise ToolError(str(exc)) from None
    except re.error as exc:
        raise ToolError(f"contains is not a valid regular expression: {exc}") from None


@mcp.tool()
def list_rpt_files() -> list:
    """RPT log files on this machine, newest first."""
    out = []
    for p in rpt.files()[:50]:
        st = p.stat()
        out.append(
            {
                "file": p.name,
                "path": str(p),
                "size": st.st_size,
                "modified": dt.datetime.fromtimestamp(st.st_mtime).isoformat(timespec="seconds"),
            }
        )
    return out


# ---------------------------------------------------------------- entry point


def main(argv: list[str] | None = None) -> None:
    global map_svg_dir, map_icon_dir
    env = os.environ.get
    parser = argparse.ArgumentParser(prog="arma3-mcp", description=__doc__)
    parser.add_argument("--host", default=env("ARMA3_MCP_HOST", "127.0.0.1"), help="extension address (ARMA3_MCP_HOST)")
    parser.add_argument("--port", type=int, default=int(env("ARMA3_MCP_PORT", "9120")), help="extension port (ARMA3_MCP_PORT)")
    parser.add_argument("--token", default=env("ARMA3_MCP_TOKEN", ""), help="extension token (ARMA3_MCP_TOKEN)")
    parser.add_argument("--rpt-dir", action="append", help="folder with .rpt logs (ARMA3_RPT_DIR); repeatable")
    parser.add_argument(
        "--map-svg-dir",
        default=env("ARMA3_MAP_SVG_DIR", str(map_svg_dir)),
        help="folder with diag_exportTerrainSvg exports (ARMA3_MAP_SVG_DIR)",
    )
    parser.add_argument(
        "--icon-dir",
        default=env("ARMA3_ICON_DIR", str(map_icon_dir)),
        help="folder with extracted Arma PNG icons (ARMA3_ICON_DIR)",
    )
    parser.add_argument("--transport", choices=["stdio", "streamable-http", "sse"], default="stdio")
    parser.add_argument("--http-host", default="127.0.0.1", help="address for the HTTP transports")
    parser.add_argument("--http-port", type=int, default=8765, help="port for the HTTP transports")
    parser.add_argument("--version", action="version", version=f"arma3-mcp {__version__}")
    args = parser.parse_args(argv)

    bridge.host, bridge.port, bridge.token = args.host, args.port, args.token
    if args.rpt_dir:
        rpt.dirs = [Path(d) for d in args.rpt_dir]
    map_svg_dir = Path(args.map_svg_dir)
    map_icon_dir = Path(args.icon_dir)

    if args.transport == "stdio":
        mcp.run()
    elif _MCP2:
        mcp.run(transport=args.transport, host=args.http_host, port=args.http_port)
    else:
        mcp.settings.host, mcp.settings.port = args.http_host, args.http_port
        mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
