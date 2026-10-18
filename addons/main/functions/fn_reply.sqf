/*
 * Hands a JSON response to the extension in pieces (callExtension arguments are kept small).
 */
params ["_id", "_json"];
private _step = 8000;
for "_i" from 0 to (count _json - 1) step _step do {
    "arma3_mcp" callExtension ["result", [_id, _json select [_i, _step]]];
};
"arma3_mcp" callExtension ["done", [_id]];
