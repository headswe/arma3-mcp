/*
 * Compiles and runs SQF, and returns the outcome as a JSON string:
 *   {"ok": true, "type": "<typeName of the result>", "time_ms": 0.1, "result": <value>}
 *   {"ok": false, "error": "<message>", "time_ms": 0.1}
 *
 * Arguments: code (string), args (passed as _this), unscheduled (true: runs atomically, in one frame).
 * Values toJSON cannot express are converted first: objects, groups, sides and the like become
 * their str form, non-finite numbers become strings, non-string hashmap keys are str'ed.
 *
 * Also sent to other machines as code (a3mcp_fnc_remoteRun), so it must not depend on other functions.
 * Locals carry an _a3mcp prefix so the code being run cannot clobber them by accident.
 */
params ["_a3mcp_code", ["_a3mcp_args", []], ["_a3mcp_unscheduled", true]];

private _a3mcp_serialize = {
    params ["_v", "_depth"];
    if (isNil "_v") exitWith { nil };
    if (_depth > 64) exitWith { str _v };
    switch (typeName _v) do {
        case "STRING";
        case "BOOL": { _v };
        case "SCALAR": { if (finite _v) then { _v } else { str _v } };
        case "ARRAY": { _v apply { [_x, _depth + 1] call _a3mcp_serialize } };
        case "HASHMAP": {
            private _out = createHashMap;
            {
                private _value = [_y, _depth + 1] call _a3mcp_serialize;
                if (!isNil "_value") then { _out set [[str _x, _x] select (_x isEqualType ""), _value] };
            } forEach _v;
            _out
        };
        default { str _v };
    };
};

private _a3mcp_start = diag_tickTime;
private _a3mcp_ok = true;
private _a3mcp_error = "";
private _a3mcp_result = nil;

private _a3mcp_fn = compile _a3mcp_code;
if (_a3mcp_fn isEqualTo {} && {(_a3mcp_code splitString (" ;" + toString [9, 10, 13])) isNotEqualTo []}) then {
    _a3mcp_ok = false;
    _a3mcp_error = "the code did not compile (syntax error); the RPT log has the details";
} else {
    private _a3mcp_run = {
        try {
            _a3mcp_result = _a3mcp_args call _a3mcp_fn;
        } catch {
            _a3mcp_ok = false;
            _a3mcp_error = if (_exception isEqualType "") then { _exception } else { str _exception };
        };
    };
    if (_a3mcp_unscheduled) then { isNil _a3mcp_run } else { call _a3mcp_run };
};

private _a3mcp_ms = round ((diag_tickTime - _a3mcp_start) * 1e6) / 1000;
if (!_a3mcp_ok) exitWith {
    toJSON createHashMapFromArray [["ok", false], ["error", _a3mcp_error], ["time_ms", _a3mcp_ms]]
};

private _a3mcp_type = if (isNil "_a3mcp_result") then { "NOTHING" } else { typeName _a3mcp_result };
private _a3mcp_value = [_a3mcp_result, 0] call _a3mcp_serialize;
private _a3mcp_json = toJSON createHashMapFromArray [["ok", true], ["type", _a3mcp_type], ["time_ms", _a3mcp_ms]];
// A hashmap cannot hold nil, so the result goes in by hand: replace the closing brace.
(_a3mcp_json select [0, count _a3mcp_json - 1]) + ",""result"":" + (if (isNil "_a3mcp_value") then { "null" } else { toJSON _a3mcp_value }) + "}"
