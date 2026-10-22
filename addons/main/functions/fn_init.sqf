/*
 * Starts the bridge on the machine that runs the extension's listener (the server, or any
 * machine with allow_client = 1 in arma3_mcp.ini). Runs at the start of every mission.
 */
private _start = "arma3_mcp" callExtension ["start", [isServer, hasInterface, isDedicated, missionName, worldName]];
_start params ["_text", "_code", "_error"];
if (_error != 0) exitWith {
    diag_log format ["[arma3_mcp] extension could not be loaded (callExtension error %1); is arma3_mcp_x64.dll/.so in the mod folder?", _error];
};
diag_log format ["[arma3_mcp] %1", _text];
if (_code != 0) exitWith {};

a3mcp_remote = createHashMap;  // request id -> [owner of the machine running it, diag_tickTime sent]

// The extension fires this as soon as a request arrives.
addMissionEventHandler ["ExtensionCallback", {
    params ["_name", "_function"];
    if (_name == "arma3_mcp" && {_function == "request"}) then { call a3mcp_fnc_drain; };
}];

// Safety net for a dropped callback; also keeps last_poll_ms_ago fresh in the extension's status.
[] spawn {
    while {true} do {
        uiSleep 0.5;
        isNil { call a3mcp_fnc_drain; call a3mcp_fnc_expireRemote };
    };
};
