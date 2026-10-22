/*
 * Runs one request from the MCP server.
 *
 * Request: {"code": "<sqf>", "args": <any, becomes _this>, "scheduled": false, "target": "local"}
 * target: "local" (this machine), "server", a client's owner id, or a player's name or UID.
 */
params ["_id", "_payload"];

private _req = fromJSON _payload;
if (isNil "_req" || {!(_req isEqualType createHashMap)}) exitWith {
    [_id, toJSON createHashMapFromArray [["ok", false], ["error", "the request is not a JSON object"]]] call a3mcp_fnc_reply;
};

private _code = _req getOrDefault ["code", ""];
private _args = _req getOrDefault ["args", []];
if (isNil "_args") then { _args = [] };
private _scheduled = _req getOrDefault ["scheduled", false];
private _owner = [_req getOrDefault ["target", "local"]] call a3mcp_fnc_target;

if (_owner isEqualType "") exitWith {
    [_id, toJSON createHashMapFromArray [["ok", false], ["error", _owner]]] call a3mcp_fnc_reply;
};

if (_owner >= 0) exitWith {
    a3mcp_remote set [_id, [_owner, diag_tickTime]];
    [[_id, _code, _args, _scheduled, a3mcp_fnc_execute, clientOwner], a3mcp_fnc_remoteRun] remoteExecCall ["call", _owner];
};

if (_scheduled) then {
    [_id, _code, _args] spawn {
        params ["_id", "_code", "_args"];
        [_id, [_code, _args, false] call a3mcp_fnc_execute] call a3mcp_fnc_reply;
    };
} else {
    [_id, [_code, _args, true] call a3mcp_fnc_execute] call a3mcp_fnc_reply;
};
