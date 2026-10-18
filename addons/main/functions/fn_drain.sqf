/*
 * Runs the requests the extension has queued, up to 50 per call.
 */
for "_n" from 1 to 50 do {
    private _head = ("arma3_mcp" callExtension ["poll", []]) select 0;
    if (_head == "") exitWith {};

    private _id = _head select [0, _head find ":"];
    private _payload = "";
    private _more = 10;
    while {_more == 10} do {
        ("arma3_mcp" callExtension ["read", [_id]]) params ["_chunk", "_code"];
        _payload = _payload + _chunk;
        _more = _code;
    };
    [_id, _payload] call a3mcp_fnc_handle;
};
