class CfgPatches {
    class arma3_mcp_main {
        name = "Arma 3 MCP";
        units[] = {};
        weapons[] = {};
        requiredVersion = 2.18;  // toJSON / fromJSON
        requiredAddons[] = {"A3_Functions_F", "3DEN"};
        author = "headswe";
        version = "0.1";
        versionStr = "0.1.0";
        versionAr[] = {0,1,0};
    };
};

class CfgFunctions {
    class a3mcp {
        tag = "a3mcp";
        class main {
            file = "\z\arma3_mcp\addons\main\functions";
            class init { postInit = 1; };
            class drain {};
            class handle {};
            class reply {};
            class target {};
            class remoteResult {};
            class expireRemote {};
            class execute { headerType = -1; };    // sent to other machines as code, so no function header
            class remoteRun { headerType = -1; };  // sent to other machines as code, so no function header
        };
    };
};

// The Eden editor runs no postInit functions of its own, so the bridge is started from its events
// too. Each waits until the function library is ready; a3mcp_fnc_init skips what is already running.
#define A3MCP_EDEN_INIT "0 spawn { waitUntil { !isNil 'a3mcp_fnc_init' }; isNil { call a3mcp_fnc_init } }"
class Cfg3DEN {
    class EventHandlers {
        class arma3_mcp {
            init = A3MCP_EDEN_INIT;
            onTerrainNew = A3MCP_EDEN_INIT;
            onMissionNew = A3MCP_EDEN_INIT;
            onMissionLoad = A3MCP_EDEN_INIT;
            onMissionPreviewEnd = A3MCP_EDEN_INIT;
        };
    };
};

// A machine that ran a request for the bridge (target = a client) sends the answer back with
// a3mcp_fnc_remoteResult. Allowed here for servers that whitelist remote execution; a mission
// with its own CfgRemoteExec in whitelist mode must list it too.
class CfgRemoteExec {
    class Functions {
        class a3mcp_fnc_remoteResult { allowedTargets = 0; jip = 0; };
    };
};
