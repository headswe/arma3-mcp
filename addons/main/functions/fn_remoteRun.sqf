/*
 * Sent as code to the machine a request targets, so that machine needs nothing installed.
 * Runs the request there and sends the answer back to the machine with the extension.
 */
params ["_id", "_code", "_args", "_scheduled", "_execute", "_replyTo"];
if (_scheduled) then {
    _this spawn {
        params ["_id", "_code", "_args", "_scheduled", "_execute", "_replyTo"];
        [_id, [_code, _args, false] call _execute] remoteExecCall ["a3mcp_fnc_remoteResult", _replyTo];
    };
} else {
    [_id, [_code, _args, true] call _execute] remoteExecCall ["a3mcp_fnc_remoteResult", _replyTo];
};
