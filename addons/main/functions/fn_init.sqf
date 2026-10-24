/*
 * Starts the bridge on the machine that runs the extension's listener (the server, or any
 * machine with allow_client = 1 in arma3_mcp.ini). Runs at the start of every mission, and in the
 * Eden editor when it opens, loads or creates a scenario, changes terrain, or returns from a
 * preview (Cfg3DEN event handlers in config.cpp).
 *
 * Safe to run more than once in the same mission: the event handler and the polling loop are
 * only added when they are not there yet.
 */
private _eden = is3DEN && {!is3DENPreview};
private _start = "arma3_mcp" callExtension ["start", [isServer, hasInterface, isDedicated, missionName, worldName, _eden]];
_start params ["_text", "_code", "_error"];
if (_error != 0) exitWith {
    diag_log format ["[arma3_mcp] extension could not be loaded (callExtension error %1); is arma3_mcp_x64.dll/.so in the mod folder?", _error];
};
diag_log format ["[arma3_mcp] %1%2", _text, ["", " (Eden editor)"] select _eden];
if (_code != 0) exitWith {};

if (isNil "a3mcp_remote") then {
    a3mcp_remote = createHashMap;  // request id -> [owner of the machine running it, diag_tickTime sent]
};

// The extension fires this as soon as a request arrives.
if (isNil "a3mcp_callbackEH") then {
    a3mcp_callbackEH = addMissionEventHandler ["ExtensionCallback", {
        params ["_name", "_function"];
        if (_name == "arma3_mcp" && {_function == "request"}) then { call a3mcp_fnc_drain; };
    }];
};

// Safety net for a dropped callback; also keeps last_poll_ms_ago fresh in the extension's status.
// uiSleep, so it keeps polling in the editor and while the game is paused.
if (isNil "a3mcp_loop" || {scriptDone a3mcp_loop}) then {
    a3mcp_loop = [] spawn {
        while {true} do {
            uiSleep 0.5;
            isNil { call a3mcp_fnc_drain; call a3mcp_fnc_expireRemote };
        };
    };
};
