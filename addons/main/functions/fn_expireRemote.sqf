/*
 * Fails requests sent to another machine that will not answer: the machine disconnected, or no
 * answer came within an hour (its mission's CfgRemoteExec blocked the call, say).
 */
if (count a3mcp_remote == 0) exitWith {};
private _connected = if (isServer) then { allUsers apply { (getUserInfo _x) select 1 } } else { [2] };
{
    _y params ["_owner", "_sent"];
    private _error = "";
    if (diag_tickTime - _sent > 3600) then { _error = "the target machine did not answer within an hour" };
    if !(_owner in _connected) then { _error = "the target machine disconnected before it answered" };
    if (_error != "") then {
        a3mcp_remote deleteAt _x;
        [_x, toJSON createHashMapFromArray [["ok", false], ["error", _error]]] call a3mcp_fnc_reply;
    };
} forEach +a3mcp_remote;
