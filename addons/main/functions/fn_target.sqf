/*
 * Resolves a request's target to the owner id of the machine that should run it:
 * -1 for this machine, or an error message.
 */
params [["_target", "local"]];
if (isNil "_target" || {_target isEqualTo "local"} || {_target isEqualTo ""}) exitWith { -1 };
if (!isMultiplayer) exitWith { -1 };

if (_target isEqualType 0) exitWith {
    if (_target == clientOwner) then { -1 } else { _target }
};
if (!(_target isEqualType "")) exitWith { "target must be ""local"", ""server"", an owner id or a player name/UID" };
if (_target == "server") exitWith {
    if (isServer) then { -1 } else { 2 }
};
if (!isServer) exitWith { "only the server can send requests to players" };

private _matches = allPlayers select { name _x == _target || {getPlayerUID _x == _target} };
if (_matches isEqualTo []) exitWith { format ["no player named %1 (allPlayers: %2)", _target, allPlayers apply { name _x }] };
private _owner = owner (_matches select 0);
if (_owner == clientOwner) then { -1 } else { _owner }
