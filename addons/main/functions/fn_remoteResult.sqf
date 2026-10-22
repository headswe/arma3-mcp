/*
 * Receives the answer to a request that ran on another machine (see a3mcp_fnc_remoteRun).
 */
params ["_id", "_json"];
if (isNil "a3mcp_remote") exitWith {};
private _owner = (a3mcp_remote getOrDefault [_id, [-100]]) select 0;
if (_owner == -100) exitWith {};
if (isRemoteExecuted && {remoteExecutedOwner != _owner}) exitWith {};
a3mcp_remote deleteAt _id;
[_id, _json] call a3mcp_fnc_reply;
